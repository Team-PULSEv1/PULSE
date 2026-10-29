"""
ai/validation.py  (NEW FILE)
Checks readings and tells apart: valid zero, valid nonzero, missing, invalid,
stale and suspiciously constant. Invalid data is NEVER turned into zero here.
"""
import json
import math

from . import config

VALID_STATUSES = ("valid_zero", "valid_nonzero")


def classify_number(value):
    """Return (status, value) for an already-parsed number (or None)."""
    if value is None:
        return "missing", None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "invalid", None
    if not math.isfinite(v):
        return "invalid", None
    if v < 0 and not config.ALLOW_NEGATIVE:
        return "invalid", None
    return ("valid_zero" if v == 0 else "valid_nonzero"), v


def classify_raw(raw):
    """
    Classify the RAW text Blynk returned for one pin.
    Follows the same unwrapping steps as parse_blynk_value() in pulse_server.py,
    but unreadable text becomes 'invalid' (not 0.0) and empty becomes 'missing'.
    """
    if raw is None:
        return "missing", None
    text = raw if isinstance(raw, str) else str(raw)
    text = text.strip()
    if text == "":
        return "missing", None
    candidate = text
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            if not parsed:
                return "missing", None
            candidate = parsed[0]
        else:
            candidate = parsed
    except ValueError:
        pass
    if candidate is None:
        return "missing", None
    s = str(candidate).strip().strip('"').strip("'").strip("[]").strip()
    if s == "" or s.lower() in ("null", "none"):
        return "missing", None
    try:
        v = float(s)
    except ValueError:
        return "invalid", None
    return classify_number(v)


def _make(ts, s1, v1, s2, v2, hw_status, source, raw_verified):
    return {"ts": float(ts), "f1": v1, "f2": v2, "f1_status": s1, "f2_status": s2,
            "hw_status": hw_status, "source": source, "raw_verified": bool(raw_verified)}


def sample_from_raw(ts, raw1, raw2, hw_status, source, raw_verified=True):
    s1, v1 = classify_raw(raw1)
    s2, v2 = classify_raw(raw2)
    return _make(ts, s1, v1, s2, v2, hw_status, source, raw_verified)


def sample_from_values(ts, f1, f2, hw_status, source, raw_verified=False):
    s1, v1 = classify_number(f1)
    s2, v2 = classify_number(f2)
    return _make(ts, s1, v1, s2, v2, hw_status, source, raw_verified)


def is_valid(sample):
    return sample["f1_status"] in VALID_STATUSES and sample["f2_status"] in VALID_STATUSES


def contiguous_valid_tail(buffer, max_gap_s=None):
    """Newest unbroken run of valid readings (stops at invalid data or a time gap)."""
    gap = config.MAX_GAP_S if max_gap_s is None else max_gap_s
    tail = []
    for s in reversed(buffer):
        if not is_valid(s):
            break
        if tail and (tail[-1]["ts"] - s["ts"]) > gap:
            break
        tail.append(s)
    tail.reverse()
    return tail


def trailing_constant_run(tail, key):
    """How many of the newest readings are EXACTLY equal, and that value."""
    if not tail:
        return 0, None
    last = tail[-1][key]
    n = 0
    for s in reversed(tail):
        if s[key] == last:
            n += 1
        else:
            break
    return n, last


def assess_quality(buffer, now):
    """
    Summarise data quality of the newest reading plus recent history.
    level: ok | no_data | stale | invalid_or_missing
    """
    q = {"level": "ok", "reasons": [], "warnings": [], "notes": [],
         "age_s": None, "constant_nonzero": [], "constant": {}}
    if not buffer:
        q["level"] = "no_data"
        q["reasons"].append("No real sensor readings have been received yet. "
                            "Nothing is being substituted.")
        return q

    latest = buffer[-1]
    q["age_s"] = max(0.0, now - latest["ts"])

    if q["age_s"] > config.STALE_AFTER_S:
        q["level"] = "stale"
        q["reasons"].append("Latest reading is %.0f s old (stale after %.0f s). "
                            "The backend may have lost its Blynk connection."
                            % (q["age_s"], config.STALE_AFTER_S))
        return q

    bad = []
    for key, label in (("f1", "Sensor 1"), ("f2", "Sensor 2")):
        st = latest[key + "_status"]
        if st not in VALID_STATUSES:
            bad.append("%s reading is %s" % (label, st))
    if bad:
        q["level"] = "invalid_or_missing"
        q["reasons"].append("; ".join(bad) + ". It was not converted to zero.")
        return q

    # Valid, fresh data: now look for warnings.
    tail = contiguous_valid_tail(buffer)
    for key, label in (("f1", "Sensor 1"), ("f2", "Sensor 2")):
        run, val = trailing_constant_run(tail, key)
        q["constant"][key] = {"run": run, "value": val}
        if run >= config.CONST_NONZERO_RUN:
            if val != 0:
                q["constant_nonzero"].append(key)
                q["warnings"].append(
                    "%s has reported exactly %s for %d readings in a row. "
                    "This is suspicious but not proof of a fault." % (label, val, run))
            else:
                q["notes"].append("%s has read zero for %d readings; a valid zero "
                                  "is not treated as a fault." % (label, run))
    if not latest.get("raw_verified", False):
        q["warnings"].append(
            "Raw-value checking is not active (the read_pin patch is not applied), "
            "so an unreadable Blynk value could appear here as a genuine zero.")
    return q
