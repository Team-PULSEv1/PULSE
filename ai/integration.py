"""
ai/integration.py  (NEW FILE)
The only module pulse_server.py talks to. It provides:
  ai_on_raw(pin, text)   - remembers the raw text Blynk returned for V0/V1
  ai_on_poll(f1, f2, st) - called once per EXISTING poll; feeds the AI + logger
  register_ai(app, state, lock, node) - adds /api/ai/status and /api/ai/data-collection
It makes NO extra Blynk requests. Every function swallows its own errors so the AI
can never crash or slow the existing polling loop or routes.
"""
import threading
import time

from flask import jsonify

from . import config
from .collector import RealDataCollector
from .monitor import AIMonitor
from .validation import sample_from_raw, sample_from_values

_PIN_KEY = {"V0": "f1", "V1": "f2"}      # matches PINS in pulse_server.py
_raw = {}
_raw_lock = threading.Lock()
_monitor = None
_collector = None
_init_lock = threading.Lock()


def get_monitor():
    global _monitor
    with _init_lock:
        if _monitor is None:
            _monitor = AIMonitor(allow_synthetic=False)
        return _monitor


def get_collector():
    global _collector
    with _init_lock:
        if _collector is None:
            _collector = RealDataCollector()
        return _collector


def ai_on_raw(pin, text):
    try:
        key = _PIN_KEY.get(pin)
        if key:
            with _raw_lock:
                _raw[key] = (time.time(), text)
    except Exception:
        pass


def ai_on_poll(f1, f2, st):
    try:
        now = time.time()
        with _raw_lock:
            r1, r2 = _raw.get("f1"), _raw.get("f2")

        def fresh(r):
            return r is not None and (now - r[0]) <= config.STALE_AFTER_S

        try:
            hw = int(st)
        except (TypeError, ValueError):
            hw = None
        if fresh(r1) and fresh(r2):
            sample = sample_from_raw(now, r1[1], r2[1], hw, config.SOURCE_LIVE, True)
        else:
            sample = sample_from_values(now, f1, f2, hw, config.SOURCE_LIVE, False)
        get_monitor().ingest(sample)
        get_collector().add(sample)
    except Exception:
        pass


def register_ai(app, state, lock, node=None):
    mon = get_monitor()
    col = get_collector()

    def _online():
        try:
            with lock:
                return bool(state.get("online"))
        except Exception:
            return None

    @app.route("/api/ai/status")
    def api_ai_status():
        try:
            return jsonify(mon.analyse(node=dict(node) if node else None,
                                       backend_online=_online()))
        except Exception as exc:
            return jsonify({"status": {"code": "DATA_QUALITY_ISSUE",
                                       "label": "DATA QUALITY ISSUE",
                                       "summary": "AI analysis failed internally."},
                            "error": type(exc).__name__}), 500

    @app.route("/api/ai/data-collection")
    def api_ai_data_collection():
        try:
            info = col.status()
            m = mon.analyse(node=None, backend_online=None)["model"]
            return jsonify({"collector": info, "model": m,
                            "source_label": config.SOURCE_LIVE})
        except Exception as exc:
            return jsonify({"error": type(exc).__name__}), 500
