"""
ai/leak_assessment.py  (NEW FILE)
Decides whether the available evidence supports a POTENTIAL leak.
Separate from anomaly detection: an anomaly alone is never called a leak.

Outcomes (code):
  NORMAL, UNUSUAL_FLOW, POTENTIAL_LEAK, INSUFFICIENT_EVIDENCE, DATA_QUALITY_ISSUE

A POTENTIAL_LEAK needs ALL of these:
  1. fresh, valid data from both sensors;
  2. the anomaly detector flagged the newest window;
  3. the difference (Sensor 1 - Sensor 2) has shifted away from its own recent
     behaviour, in the same direction, for PERSIST_EVALS evaluations in a row;
  4. both flows are steady (so it is not just a start-up/stop transient, when
     two sensors normally disagree because of transit delay);
  5. neither sensor is suspiciously constant (a stuck sensor would look the same).
Even then the result is "requires investigation", never "confirmed".
No leak probability or confidence percentage is produced - none could be
validated with the data available.
"""
import numpy as np

from . import config
from .anomaly import robust_center_scale

LABELS = {
    "NORMAL": "NORMAL",
    "UNUSUAL_FLOW": "ANOMALY DETECTED",
    "POTENTIAL_LEAK": "POTENTIAL LEAK",
    "INSUFFICIENT_EVIDENCE": "INSUFFICIENT EVIDENCE",
    "DATA_QUALITY_ISSUE": "DATA QUALITY ISSUE",
}


def _rel_std(values):
    arr = np.asarray(values, dtype=float)
    mean = abs(float(arr.mean()))
    std = float(arr.std())
    return std <= config.STEADY_REL_STD * mean if mean > 0 else std == 0.0


def imbalance_pattern(tail):
    """Test for a persistent, steady shift in (Sensor 1 - Sensor 2). Pure function of `tail`."""
    W, P = config.WINDOW, config.PERSIST_EVALS
    need = W + P - 1 + config.BASELINE_MIN_HISTORY
    out = {"evaluable": False, "present": False}
    if len(tail) < need:
        out["needed_samples"] = need - len(tail)
        return out
    ref = tail[:-(W + P - 1)]
    r1 = [s["f1"] for s in ref]
    r2 = [s["f2"] for s in ref]
    rd = [a - b for a, b in zip(r1, r2)]
    flow_level = (abs(float(np.median(r1))) + abs(float(np.median(r2)))) / 2.0
    med_d, scale_d = robust_center_scale(rd, flow_level)
    med1, sc1 = robust_center_scale(r1)
    med2, sc2 = robust_center_scale(r2)

    holds, signs = [], []
    latest = {}
    for k in range(P):                       # k = 0 is the newest window
        end = len(tail) - k
        win = tail[end - W:end]
        w1 = [s["f1"] for s in win]
        w2 = [s["f2"] for s in win]
        dmean = float(np.mean(w1)) - float(np.mean(w2))
        z = (dmean - med_d) / scale_d
        steady = _rel_std(w1) and _rel_std(w2)
        holds.append(abs(z) >= config.BASELINE_Z and steady)
        signs.append(1 if z > 0 else -1)
        if k == 0:
            z1 = (float(np.mean(w1)) - med1) / sc1
            z2 = (float(np.mean(w2)) - med2) / sc2
            latest = {"diff_ref_median": round(med_d, 4), "diff_window_mean": round(dmean, 4),
                      "diff_shift": round(dmean - med_d, 4), "diff_z": round(z, 2),
                      "steady": steady,
                      "sensor1_z": round(z1, 2), "sensor2_z": round(z2, 2),
                      "both_sensors_moved": bool(abs(z1) >= config.BASELINE_Z and
                                                 abs(z2) >= config.BASELINE_Z)}
    out.update(latest)
    out["evaluable"] = True
    out["present"] = bool(all(holds) and len(set(signs)) == 1)
    out["consecutive_evaluations_met"] = _leading_true(holds)
    return out


def _leading_true(flags):
    n = 0
    for f in flags:
        if f:
            n += 1
        else:
            break
    return n


def assess(quality, ml, base, tail):
    """Return {code, label, summary, explanation, evidence}."""
    def result(code, summary, explanation, evidence=None):
        return {"code": code, "label": LABELS[code], "summary": summary,
                "explanation": explanation, "evidence": evidence or {}}

    # 1. Data quality always comes first.
    if quality["level"] != "ok":
        return result("DATA_QUALITY_ISSUE", quality["reasons"][0] if quality["reasons"]
                      else "Data quality problem.",
                      list(quality["reasons"]) +
                      ["Flow analysis is paused until valid, fresh readings return."])

    # 2. Which anomaly result do we rely on?
    det = ml if (ml is not None and ml["evaluated"]) else base
    if not det["evaluated"]:
        return result("INSUFFICIENT_EVIDENCE",
                      "Not enough valid history to assess flow yet.",
                      list(det["reasons"]),
                      {"detector": det["method"], **det.get("detail", {})})

    if not det["is_anomaly"]:
        return result("NORMAL",
                      "No unusual flow detected by the %s method." %
                      ("trained model" if det["trained"] else "recent-history baseline"),
                      list(det["reasons"]),
                      {"detector": det["method"]})

    # 3. An anomaly exists. Does the evidence support a POTENTIAL leak?
    pat = imbalance_pattern(tail)
    evidence = {"detector": det["method"], "imbalance": pat}
    expl = ["Unusual flow was flagged (%s)." % det["method"]] + list(det["reasons"])
    stuck = quality["constant_nonzero"]

    if pat["evaluable"] and pat["present"] and stuck:
        names = " and ".join("Sensor %s" % k[1] for k in stuck)
        expl.append("The sensors disagree, but %s is suspiciously constant, so a stuck sensor "
                    "cannot be ruled out. Check the sensor before drawing a conclusion." % names)
        return result("INSUFFICIENT_EVIDENCE",
                      "Sensor disagreement, but a stuck sensor cannot be ruled out.",
                      expl, evidence)

    if pat["evaluable"] and pat["present"]:
        expl.append("(Sensor 1 - Sensor 2) shifted from about %s to %s and has stayed "
                    "shifted for %d evaluations while both flows were steady."
                    % (pat["diff_ref_median"], pat["diff_window_mean"],
                       config.PERSIST_EVALS))
        expl.append("This is consistent with flow being lost between the sensors, but also "
                    "with sensor drift, calibration differences or partial blockage. "
                    "It needs investigation; it is NOT a confirmed leak. Sensor placement "
                    "is unverified.")
        return result("POTENTIAL_LEAK", "Possible leak - requires investigation.",
                      expl, evidence)

    if not pat["evaluable"]:
        expl.append("Not enough history yet to test whether the sensors' relationship "
                    "changed (%d more readings needed), so no leak assessment is possible."
                    % pat["needed_samples"])
    elif pat.get("both_sensors_moved"):
        expl.append("Both sensors changed together; this looks like a change in overall "
                    "flow, not a loss between sensors.")
    elif not pat.get("steady", True):
        expl.append("Flows are still changing, so a disagreement between sensors is "
                    "expected (transit delay) and is not treated as leak evidence.")
    else:
        expl.append("The sensors' relationship has not shifted persistently, so the "
                    "evidence does not point to a leak.")
    return result("UNUSUAL_FLOW", "Unusual flow behaviour detected; no leak evidence.",
                  expl, evidence)
