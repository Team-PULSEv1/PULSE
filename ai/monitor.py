"""
ai/monitor.py  (NEW FILE)
Holds the recent validated readings and produces the AI status payload.
A LIVE monitor refuses any reading not labelled 'blynk_live', so synthetic data
can never reach the dashboard. Tests use a separate monitor with allow_synthetic=True.
"""
import os
import threading
import time
from collections import deque

from . import config
from .anomaly import RecentHistoryBaseline, load_detector
from .leak_assessment import assess
from .localisation import localise
from .validation import assess_quality, contiguous_valid_tail


class AIMonitor:
    def __init__(self, allow_synthetic=False, now_fn=time.time, model_path=None,
                 load_model=True):
        self.allow_synthetic = allow_synthetic
        self.now_fn = now_fn
        self.model_path = model_path or config.MODEL_PATH
        self._buf = deque(maxlen=config.BUFFER_LEN)
        self._lock = threading.RLock()
        self._baseline = RecentHistoryBaseline()
        self._detector, self._model_info = (None, {"ready": False,
                                                   "message": "Model loading disabled."})
        self._model_mtime = None
        self.rejected = 0
        self.last_rejection = None
        if load_model:
            self._reload_model()

    # ---- model handling -------------------------------------------------
    def _reload_model(self):
        self._detector, self._model_info = load_detector(self.model_path)
        try:
            self._model_mtime = os.path.getmtime(self.model_path)
        except OSError:
            self._model_mtime = None

    def _maybe_reload_model(self):
        try:
            mt = os.path.getmtime(self.model_path)
        except OSError:
            mt = None
        if mt != self._model_mtime:
            self._reload_model()

    # ---- input ----------------------------------------------------------
    def ingest(self, sample):
        """Add one validated sample. Returns False if rejected (wrong source)."""
        allowed = {config.SOURCE_LIVE}
        if self.allow_synthetic:
            allowed.add(config.SOURCE_SYNTHETIC)
        if sample.get("source") not in allowed:
            with self._lock:
                self.rejected += 1
                self.last_rejection = "Rejected a reading labelled '%s'." % sample.get("source")
            return False
        with self._lock:
            self._buf.append(sample)
        return True

    # ---- output ---------------------------------------------------------
    def analyse(self, node=None, backend_online=None):
        with self._lock:
            buf = list(self._buf)
        self._maybe_reload_model()
        now = self.now_fn()
        quality = assess_quality(buf, now)
        tail = contiguous_valid_tail(buf)

        base = self._baseline.evaluate(tail) if quality["level"] == "ok" else None
        ml = self._detector.evaluate(tail) if (self._detector and quality["level"] == "ok") else None
        if base is None:
            base = {"method": self._baseline.method, "trained": False, "evaluated": False,
                    "is_anomaly": None, "reasons": [], "detail": {}}
        a = assess(quality, ml, base, tail)
        loc = localise(a["code"], a["evidence"], node)

        latest = buf[-1] if buf else None
        sensors = {"sensor1": None, "sensor2": None, "timestamp": None, "age_s": None,
                   "fresh": False, "valid": False, "units": "unverified"}
        if latest:
            sensors.update({
                "sensor1": {"value": latest["f1"], "status": latest["f1_status"]},
                "sensor2": {"value": latest["f2"], "status": latest["f2_status"]},
                "timestamp": latest["ts"], "age_s": quality["age_s"],
                "fresh": quality["age_s"] is not None and quality["age_s"] <= config.STALE_AFTER_S,
                "valid": latest["f1_status"] in ("valid_zero", "valid_nonzero")
                         and latest["f2_status"] in ("valid_zero", "valid_nonzero")})

        needed = config.WINDOW + config.BASELINE_MIN_HISTORY
        used = ml if (ml and ml["evaluated"]) else base
        warnings = list(quality["warnings"])
        if self.last_rejection:
            warnings.append(self.last_rejection)
        return {
            "mode": "synthetic_test" if self.allow_synthetic else "live",
            "source": latest["source"] if latest else None,
            "generated_ts": now,
            "backend_online": backend_online,
            "hardware_status_code": latest["hw_status"] if latest else None,
            "sensors": sensors,
            "status": {"code": a["code"], "label": a["label"], "summary": a["summary"]},
            "explanation": a["explanation"],
            "warnings": warnings,
            "notes": quality["notes"],
            "history": {"contiguous_valid_readings": len(tail), "needed_for_baseline": needed,
                        "sufficient": len(tail) >= needed, "buffered": len(buf)},
            "model": {**self._model_info, "method_in_use": used["method"],
                      "trained_model_in_use": bool(ml and ml["evaluated"])},
            "anomaly": {"method": used["method"], "trained": used["trained"],
                        "evaluated": used["evaluated"], "is_anomaly": used["is_anomaly"]},
            "evidence": a["evidence"],
            "localisation": loc,
            "limitations": list(config.LIMITATIONS),
        }
