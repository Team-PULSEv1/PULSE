"""
ai/localisation.py  (NEW FILE)
Reports leak LOCATION only as far as the verified sensor layout allows.

Today the layout is unknown, so the answer is always:
  "Anomaly detected; precise leak location cannot currently be determined."
The fixed project location is shown for context only. It is NOT a leak location.
No coordinates, pipeline sections or sensor positions are invented.
"""
from . import config

UNKNOWN_MESSAGE = "Anomaly detected; precise leak location cannot currently be determined."


def localise(status_code, evidence, node=None):
    project = None
    if node:
        project = {"name": node.get("name"), "lat": node.get("lat"), "lng": node.get("lng"),
                   "note": "Fixed project location - NOT the location of a leak."}
    out = {"status": "not_applicable", "message": "", "section": None,
           "project_location": project, "layout": config.SENSOR_LAYOUT,
           "how_to_enable": ("Verify the physical sensor layout, then set PULSE_SENSOR_LAYOUT="
                             + config.LAYOUT_S1_UPSTREAM + " only if Sensor 1 is upstream of "
                             "Sensor 2 on the same pipe. Locating a leak along the pipe needs "
                             "more instrumentation (e.g. several flow or pressure sensors with "
                             "known positions).")}
    if status_code == "NORMAL":
        out["message"] = "No anomaly; localisation not applicable."
        return out
    if status_code in ("DATA_QUALITY_ISSUE", "INSUFFICIENT_EVIDENCE"):
        out["status"] = "unavailable"
        out["message"] = "Localisation unavailable: not enough reliable evidence."
        return out

    # UNUSUAL_FLOW or POTENTIAL_LEAK
    out["status"] = "unknown"
    out["message"] = UNKNOWN_MESSAGE
    if status_code == "POTENTIAL_LEAK" and config.SENSOR_LAYOUT == config.LAYOUT_S1_UPSTREAM:
        shift = ((evidence or {}).get("imbalance") or {}).get("diff_shift")
        if shift is not None and shift > 0:
            out["status"] = "section_estimate"
            out["section"] = "between Sensor 1 and Sensor 2"
            out["message"] = ("Evidence is consistent with loss somewhere between Sensor 1 and "
                              "Sensor 2 (section only; no coordinates, exact point unknown).")
        elif shift is not None:
            out["message"] = ("Sensor 2 now reads higher than Sensor 1, which a loss between "
                              "them would not cause. Check sensor calibration. " + UNKNOWN_MESSAGE)
    return out
