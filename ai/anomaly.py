"""
ai/anomaly.py  (NEW FILE)
Two ways to spot unusual flow. Neither one says "leak" - that is a separate module.

1. RecentHistoryBaseline  - a plain statistical BASELINE, NOT a trained model.
   It compares the newest ~20 s with the sensors' own earlier readings.
   It detects CHANGES, not "abnormal levels": if a problem has lasted longer than
   the history held in memory (about 20 min) it becomes the new reference and
   this baseline stops flagging it.
2. IForestDetector - an Isolation Forest trained ONLY on real data you collected
   (see train_model.py). It is used only when a model file exists and is valid.
"""
import os

import numpy as np

from . import config
from .features import FEATURE_NAMES, window_features


def robust_center_scale(values, floor_ref=None):
    """Median and a 'robust spread' (MAD-based) with a floor so it is never ~0."""
    arr = np.asarray(values, dtype=float)
    med = float(np.median(arr))
    mad = float(np.median(np.abs(arr - med)))
    ref = abs(med) if floor_ref is None else abs(floor_ref)
    scale = max(1.4826 * mad, config.BASELINE_REL_FLOOR * ref, config.BASELINE_ABS_FLOOR)
    return med, scale


def _empty(method, trained):
    return {"method": method, "trained": trained, "evaluated": False,
            "is_anomaly": None, "reasons": [], "detail": {}}


class RecentHistoryBaseline:
    method = "baseline_recent_history"

    def evaluate(self, tail):
        out = _empty(self.method, False)
        W = config.WINDOW
        need_total = W + config.BASELINE_MIN_HISTORY
        if len(tail) < need_total:
            need = need_total - len(tail)
            out["detail"]["needed_samples"] = need
            out["reasons"].append(
                "Collecting history: %d more consecutive valid readings are needed "
                "before the baseline can compare recent flow with earlier flow." % need)
            return out

        window, ref = tail[-W:], tail[:-W]
        r1 = [s["f1"] for s in ref]
        r2 = [s["f2"] for s in ref]
        rd = [a - b for a, b in zip(r1, r2)]
        med1 = float(np.median(r1))
        med2 = float(np.median(r2))
        flow_level = (abs(med1) + abs(med2)) / 2.0

        w1 = [s["f1"] for s in window]
        w2 = [s["f2"] for s in window]
        wd = [a - b for a, b in zip(w1, w2)]

        checks = (("Sensor 1", w1, r1, None),
                  ("Sensor 2", w2, r2, None),
                  ("Difference (Sensor 1 - Sensor 2)", wd, rd, flow_level))
        flagged = False
        z_values = {}
        for label, wv, rv, floor_ref in checks:
            med, scale = robust_center_scale(rv, floor_ref)
            wmean = float(np.mean(wv))
            z = (wmean - med) / scale
            z_values[label] = round(z, 2)
            if abs(z) >= config.BASELINE_Z:
                flagged = True
                out["reasons"].append(
                    "%s averaged %.4g over the last %d readings versus a recent "
                    "typical %.4g (%.1f robust spreads away)." % (label, wmean, W, med, z))
        out["evaluated"] = True
        out["is_anomaly"] = flagged
        out["detail"]["z"] = z_values
        if not flagged:
            out["reasons"].append(
                "No unusual change compared with the last %d readings. "
                "This is a change detector; it does not show that flow is within a "
                "normal operating range (that range is unknown)." % len(ref))
        return out


class IForestDetector:
    method = "isolation_forest"

    def __init__(self, bundle):
        self.model = bundle["model"]
        self.meta = bundle["meta"]

    def evaluate(self, tail):
        out = _empty(self.method, True)
        W = config.WINDOW
        if len(tail) < W:
            out["reasons"].append("Need %d consecutive valid readings." % W)
            return out
        win = tail[-W:]
        feats = window_features([s["f1"] for s in win], [s["f2"] for s in win])
        X = np.asarray([feats])
        pred = int(self.model.predict(X)[0])
        score = float(self.model.score_samples(X)[0])
        out["evaluated"] = True
        out["is_anomaly"] = (pred == -1)
        out["detail"] = {"score": round(score, 4),
                         "note": "Lower score = more unusual compared with training data. "
                                 "Not a probability."}
        if out["is_anomaly"]:
            med = self.meta["feature_median"]
            sc = self.meta["feature_scale"]
            zs = sorted(((abs((f - m) / s), n, f) for f, m, s, n in
                         zip(feats, med, sc, FEATURE_NAMES)), reverse=True)
            top = [t for t in zs[:3] if t[0] >= 3.0]
            if top:
                for z, name, val in top:
                    out["reasons"].append(
                        "Feature '%s' = %.4g is %.1f robust spreads from the training data."
                        % (name, val, z))
            else:
                out["reasons"].append("The combination of features is unusual compared "
                                      "with training data; no single feature stands out.")
        else:
            out["reasons"].append("Recent flow resembles the real data this model was "
                                  "trained on.")
        return out


def load_detector(path=None):
    """Return (detector or None, info dict). Never raises."""
    path = path or config.MODEL_PATH
    info = {"ready": False, "message": "", "path_name": os.path.basename(path)}
    if not os.path.exists(path):
        info["message"] = ("No trained model yet. Collect real data, then run "
                           "'python -m ai.train_model'.")
        return None, info
    try:
        import joblib                         # installed together with scikit-learn
        bundle = joblib.load(path)            # only load model files YOU created
        meta = bundle["meta"]
        if meta.get("feature_names") != FEATURE_NAMES or meta.get("window") != config.WINDOW:
            info["message"] = "Model file does not match the current feature settings; retrain."
            return None, info
        info.update({"ready": True,
                     "message": "Model trained on real logged data.",
                     "trained_at_utc": meta.get("trained_at_utc"),
                     "n_train_windows": meta.get("n_train_windows"),
                     "valid_hours": round(meta.get("valid_seconds", 0) / 3600.0, 2),
                     "contamination": meta.get("contamination")})
        return IForestDetector(bundle), info
    except Exception as exc:
        info["message"] = "Model file could not be loaded (%s)." % type(exc).__name__
        return None, info
