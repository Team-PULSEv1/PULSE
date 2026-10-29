"""
tests/test_ai.py  (NEW FILE)
Automated tests for the PULSE AI extension. Uses only Python's built-in unittest.

!! ALL DATA IN THIS FILE IS SYNTHETIC !!
Every sample is labelled source='synthetic_test'. Synthetic results show that the
CODE behaves as designed (prototype behaviour). They are NOT evidence of real-world
leak-detection performance and must never be shown as real sensor readings.

Run from the folder that contains pulse_server.py and the ai/ folder:
    python -m unittest discover -s tests -v
"""
import csv
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai import config                                            # noqa: E402
from ai import integration                                       # noqa: E402
from ai.anomaly import RecentHistoryBaseline, load_detector      # noqa: E402
from ai.collector import RealDataCollector, FIELDS               # noqa: E402
from ai.features import FEATURE_NAMES, window_features           # noqa: E402
from ai.localisation import localise, UNKNOWN_MESSAGE            # noqa: E402
from ai.monitor import AIMonitor                                 # noqa: E402
from ai.train_model import TrainingError, build_training_matrix, train  # noqa: E402
from ai.validation import (assess_quality, classify_number, classify_raw,  # noqa: E402
                           contiguous_valid_tail, sample_from_raw, sample_from_values)

SYN = config.SOURCE_SYNTHETIC
STEP = config.POLL_INTERVAL_S
T0 = 1_000_000.0


# ----------------------------------------------------------------------
# SYNTHETIC data helpers (never used outside tests)
# ----------------------------------------------------------------------
def syn_normal(n, seed=0, level=10.0):
    """SYNTHETIC steady flow; Sensor 2 reads 97 % of Sensor 1."""
    rng = np.random.default_rng(seed)
    f1 = level + rng.normal(0, 0.05, n)
    f2 = 0.97 * f1 + rng.normal(0, 0.05, n)
    return list(zip(f1, f2))


def syn_leak(n_normal, n_leak, drop=0.2, seed=1):
    """SYNTHETIC: Sensor 2 loses `drop` of its flow after n_normal readings."""
    pairs = syn_normal(n_normal + n_leak, seed)
    return [(a, b if i < n_normal else b * (1 - drop)) for i, (a, b) in enumerate(pairs)]


def syn_common_mode(n_normal, n_after, factor=1.6, seed=2):
    """SYNTHETIC: both sensors rise together (demand change)."""
    pairs = syn_normal(n_normal + n_after, seed)
    return [(a, b) if i < n_normal else (a * factor, b * factor) for i, (a, b) in enumerate(pairs)]


def syn_lag_transient(n_normal, lag=6, n_after=54, seed=4):
    """SYNTHETIC: Sensor 1 steps up 1.5x; Sensor 2 follows `lag` readings later."""
    pairs = syn_normal(n_normal + n_after, seed)
    out = []
    for i, (a, b) in enumerate(pairs):
        if i < n_normal:
            out.append((a, b))
        elif i < n_normal + lag:
            out.append((a * 1.5, b))
        else:
            out.append((a * 1.5, b * 1.5))
    return out


def feed(pairs, monitor, start=T0, hw=1, source=SYN):
    """Feed (f1, f2) pairs into a monitor; return the last timestamp."""
    ts = start
    for a, b in pairs:
        monitor.ingest(sample_from_values(ts, a, b, hw, source))
        ts += STEP
    return ts - STEP


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def new_monitor(clock=None, **kw):
    clock = clock or Clock(T0)
    m = AIMonitor(allow_synthetic=True, now_fn=clock, load_model=kw.pop("load_model", False), **kw)
    return m, clock


def run_scenario(pairs, **kw):
    m, clock = new_monitor(**kw)
    last = feed(pairs, m)
    clock.t = last + 1.0
    return m.analyse()


# ----------------------------------------------------------------------
class TestValidation(unittest.TestCase):                       # items 1-5
    def test_valid_zero_is_not_a_fault(self):
        self.assertEqual(classify_raw("0"), ("valid_zero", 0.0))
        self.assertEqual(classify_raw("0.0"), ("valid_zero", 0.0))
        self.assertEqual(classify_number(0), ("valid_zero", 0.0))

    def test_valid_nonzero(self):
        self.assertEqual(classify_raw("1.5"), ("valid_nonzero", 1.5))
        self.assertEqual(classify_raw('"2.0"'), ("valid_nonzero", 2.0))
        self.assertEqual(classify_raw("[3]"), ("valid_nonzero", 3.0))

    def test_missing_and_invalid_are_not_zero(self):
        for raw in ("", "   ", "null", "[]", None):
            status, val = classify_raw(raw)
            self.assertEqual(status, "missing", raw)
            self.assertIsNone(val)
        for raw in ("abc", "nan", "inf", "-1", "NaN", "1e999"):
            status, val = classify_raw(raw)
            self.assertEqual(status, "invalid", raw)
            self.assertIsNone(val)          # never silently becomes 0.0

    def test_zero_flow_stream_is_ok_not_data_quality_issue(self):
        buf = [sample_from_values(T0 + i * STEP, 0.0, 0.0, 0, SYN) for i in range(50)]
        q = assess_quality(buf, now=buf[-1]["ts"] + 1)
        self.assertEqual(q["level"], "ok")
        self.assertEqual(q["constant_nonzero"], [])       # constant ZERO is not suspicious
        self.assertTrue(any("valid zero" in n for n in q["notes"]))

    def test_invalid_latest_reading_flags_data_quality(self):
        buf = [sample_from_values(T0, 5.0, 5.0, 1, SYN),
               sample_from_raw(T0 + STEP, "abc", "5.0", 1, SYN)]
        q = assess_quality(buf, now=T0 + STEP + 1)
        self.assertEqual(q["level"], "invalid_or_missing")
        self.assertIsNone(buf[-1]["f1"])

    def test_stale_reading(self):
        buf = [sample_from_values(T0, 5.0, 5.0, 1, SYN)]
        q = assess_quality(buf, now=T0 + config.STALE_AFTER_S + 5)
        self.assertEqual(q["level"], "stale")

    def test_no_data_is_reported_not_substituted(self):
        q = assess_quality([], now=T0)
        self.assertEqual(q["level"], "no_data")

    def test_suspiciously_constant_nonzero_is_warning_only(self):
        n = config.CONST_NONZERO_RUN + 5
        buf = [sample_from_values(T0 + i * STEP, 7.0, 6.0 + 0.01 * (i % 5), 1, SYN) for i in range(n)]
        q = assess_quality(buf, now=buf[-1]["ts"] + 1)
        self.assertEqual(q["level"], "ok")                 # not automatically a fault
        self.assertEqual(q["constant_nonzero"], ["f1"])
        self.assertTrue(q["warnings"])

    def test_gap_breaks_contiguous_tail(self):
        a = [sample_from_values(T0 + i * STEP, 1, 1, 1, SYN) for i in range(5)]
        b = [sample_from_values(T0 + 1000 + i * STEP, 1, 1, 1, SYN) for i in range(3)]
        self.assertEqual(len(contiguous_valid_tail(a + b)), 3)


class TestFeatures(unittest.TestCase):                         # item 6
    def test_feature_values(self):
        f1 = [1, 2, 3, 4]
        f2 = [1, 1, 1, 1]
        feats = dict(zip(FEATURE_NAMES, window_features(f1, f2)))
        self.assertAlmostEqual(feats["f1_last"], 4)
        self.assertAlmostEqual(feats["diff_last"], 3)
        self.assertAlmostEqual(feats["f1_mean"], 2.5)
        self.assertAlmostEqual(feats["diff_mean"], 1.5)
        self.assertAlmostEqual(feats["f1_slope"], 1.0)
        self.assertAlmostEqual(feats["f2_slope"], 0.0)
        self.assertAlmostEqual(feats["f2_std"], 0.0)

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            window_features([1, 2, 3], [1, 2])


class TestInsufficientHistory(unittest.TestCase):              # item 7
    def test_short_history(self):
        p = run_scenario(syn_normal(20))
        self.assertEqual(p["status"]["code"], "INSUFFICIENT_EVIDENCE")
        self.assertFalse(p["history"]["sufficient"])
        self.assertIn("more consecutive valid readings", " ".join(p["explanation"]))

    def test_baseline_does_not_claim_to_be_trained(self):
        p = run_scenario(syn_normal(100))
        self.assertFalse(p["anomaly"]["trained"])
        self.assertFalse(p["model"]["ready"])
        self.assertEqual(p["model"]["method_in_use"], "baseline_recent_history")


class TestAnomalyAndLeakSeparation(unittest.TestCase):         # items 8-10
    def test_normal_flow(self):
        p = run_scenario(syn_normal(200))
        self.assertEqual(p["status"]["code"], "NORMAL")

    def test_simulated_leak_gives_potential_leak(self):
        p = run_scenario(syn_leak(150, 40, drop=0.2))
        self.assertEqual(p["status"]["code"], "POTENTIAL_LEAK")
        text = " ".join(p["explanation"])
        self.assertIn("NOT a confirmed leak", text)
        self.assertNotIn("confidence", text.lower())
        self.assertNotIn("probability", str(p["status"]).lower())

    def test_common_mode_change_is_anomaly_not_leak(self):
        p = run_scenario(syn_common_mode(150, 40))
        self.assertEqual(p["status"]["code"], "UNUSUAL_FLOW")
        self.assertNotEqual(p["status"]["code"], "POTENTIAL_LEAK")

    def test_single_spike_is_not_a_leak(self):
        pairs = syn_normal(200)
        pairs[-1] = (pairs[-1][0], pairs[-1][1] * 0.3)
        p = run_scenario(pairs)
        self.assertNotEqual(p["status"]["code"], "POTENTIAL_LEAK")

    def test_lagging_sensor_transient_is_not_a_leak(self):
        pairs = syn_normal(150)
        # Sensor 1 steps up; Sensor 2 follows 6 readings later (transit delay).
        step = [(a * 1.5, b) for a, b in syn_normal(6, 7)] + \
               [(a * 1.5, b * 1.5) for a, b in syn_normal(20, 8)]
        p = run_scenario(pairs + step)
        self.assertNotEqual(p["status"]["code"], "POTENTIAL_LEAK")

    def test_stuck_sensor_blocks_leak_claim(self):
        pairs = syn_normal(150)
        # Sensor 1 frozen at one exact nonzero value while Sensor 2 falls.
        after = [(10.0, 10.0 * 0.97 * 0.8 + 0.001 * (i % 3)) for i in range(40)]
        p = run_scenario(pairs + after)
        self.assertEqual(p["status"]["code"], "INSUFFICIENT_EVIDENCE")
        self.assertTrue(p["warnings"])

    def test_invalid_data_gives_data_quality_not_leak(self):
        m, clock = new_monitor()
        last = feed(syn_normal(100), m)
        m.ingest(sample_from_raw(last + STEP, "abc", "9.0", 1, SYN))
        clock.t = last + STEP + 1
        p = m.analyse()
        self.assertEqual(p["status"]["code"], "DATA_QUALITY_ISSUE")
        self.assertIsNone(p["sensors"]["sensor1"]["value"])

    def test_zero_flow_system_is_normal(self):
        p = run_scenario([(0.0, 0.0)] * 120)
        self.assertEqual(p["status"]["code"], "NORMAL")

    def test_stale_data_gives_data_quality(self):
        m, clock = new_monitor()
        last = feed(syn_normal(100), m)
        clock.t = last + 60
        self.assertEqual(m.analyse()["status"]["code"], "DATA_QUALITY_ISSUE")

    def test_baseline_alone_flags_step_change(self):
        tail = [sample_from_values(T0 + i * STEP, a, b, 1, SYN)
                for i, (a, b) in enumerate(syn_common_mode(100, 20))]
        res = RecentHistoryBaseline().evaluate(tail)
        self.assertTrue(res["evaluated"])
        self.assertTrue(res["is_anomaly"])
        self.assertTrue(res["reasons"])


class TestCollector(unittest.TestCase):                        # item 11
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "log.csv")

    def tearDown(self):
        self.dir.cleanup()

    def live(self, ts, a, b, hw=1):
        return sample_from_values(ts, a, b, hw, config.SOURCE_LIVE, raw_verified=True)

    def rows(self):
        with open(self.path, newline="") as fh:
            return list(csv.DictReader(fh))

    def test_writes_header_timestamp_and_fields(self):
        c = RealDataCollector(self.path, heartbeat_s=10)
        self.assertEqual(c.add(self.live(T0, 5.0, 4.8)), "first")
        r = self.rows()[0]
        self.assertEqual(list(r.keys()), FIELDS)
        self.assertEqual(r["source"], "blynk_live")
        self.assertEqual(float(r["ts_epoch"]), T0)
        self.assertTrue(r["ts_utc"].startswith("1970-01-12"))
        self.assertEqual(r["valid"], "1")

    def test_duplicates_skipped_but_heartbeat_and_changes_written(self):
        c = RealDataCollector(self.path, heartbeat_s=10)
        c.add(self.live(T0, 5, 5))
        self.assertIsNone(c.add(self.live(T0 + 2, 5, 5)))
        self.assertIsNone(c.add(self.live(T0 + 4, 5, 5)))
        self.assertEqual(c.add(self.live(T0 + 12, 5, 5)), "heartbeat")
        self.assertEqual(c.add(self.live(T0 + 14, 6, 5)), "change")
        self.assertEqual(len(self.rows()), 3)
        self.assertEqual(self.rows()[1]["since_last_row_s"], "12.0")

    def test_invalid_reading_is_logged_as_invalid_not_zero(self):
        c = RealDataCollector(self.path)
        c.add(sample_from_raw(T0, "abc", "3", 1, config.SOURCE_LIVE))
        r = self.rows()[0]
        self.assertEqual(r["sensor1"], "")
        self.assertEqual(r["sensor1_status"], "invalid")
        self.assertEqual(r["valid"], "0")

    def test_valid_zero_logged_as_valid(self):
        c = RealDataCollector(self.path)
        c.add(self.live(T0, 0.0, 0.0, hw=0))
        r = self.rows()[0]
        self.assertEqual(r["sensor1_status"], "valid_zero")
        self.assertEqual(r["valid"], "1")

    def test_synthetic_rows_refused_and_no_credentials_in_file(self):
        c = RealDataCollector(self.path)
        self.assertIsNone(c.add(sample_from_values(T0, 5, 5, 1, SYN)))
        self.assertEqual(c.rows_rejected, 1)
        self.assertFalse(os.path.exists(self.path))
        c.add(self.live(T0, 5, 5))
        with open(self.path) as fh:
            content = fh.read().lower()
        for word in ("token", "password", "gnylh1", "blynk.cloud"):
            self.assertNotIn(word, content)

    def test_disk_error_does_not_raise(self):
        c = RealDataCollector(os.path.join(self.dir.name, "x", "y.csv"))
        with mock.patch("os.makedirs", side_effect=OSError("disk full")):
            self.assertIsNone(c.add(self.live(T0, 1, 1)))
        self.assertIn("OSError", c.last_error)


def syn_training_rows(n=1500, seed=3):
    """SYNTHETIC log rows (strings, like a CSV) for training tests only."""
    rng = np.random.default_rng(seed)
    level = 10 + np.cumsum(rng.normal(0, 0.05, n))
    level = 10 + (level - 10) * 0.2
    rows = []
    for i in range(n):
        f1 = level[i] + rng.normal(0, 0.05)
        f2 = 0.97 * level[i] + rng.normal(0, 0.05)
        rows.append({"ts_epoch": str(T0 + i * STEP), "_ts": T0 + i * STEP, "source": SYN,
                     "sensor1": str(f1), "sensor2": str(f2), "valid": "1", "hw_status": "1"})
    return rows


class TestTrainingAndModel(unittest.TestCase):                 # items 8 & model loads
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.model_path = os.path.join(self.dir.name, "m.joblib")
        self.p1 = mock.patch.object(config, "MIN_TRAIN_VALID_SECONDS", 600)
        self.p2 = mock.patch.object(config, "MIN_TRAIN_WINDOWS", 500)
        self.p1.start()
        self.p2.start()

    def tearDown(self):
        self.p1.stop()
        self.p2.stop()
        self.dir.cleanup()

    def test_refuses_synthetic_rows_by_default(self):
        with self.assertRaises(TrainingError):
            build_training_matrix(syn_training_rows())

    def test_refuses_too_little_data(self):
        with self.assertRaises(TrainingError):
            build_training_matrix(syn_training_rows(100), allow_synthetic=True)

    def test_refuses_nearly_all_zero_flow(self):
        rows = syn_training_rows()
        for r in rows:
            r["sensor1"] = r["sensor2"] = "0.0"
        with self.assertRaises(TrainingError):
            build_training_matrix(rows, allow_synthetic=True)

    def test_excludes_invalid_and_alert_rows_and_does_not_fill_gaps(self):
        rows = syn_training_rows()
        rows[700]["valid"] = "0"
        rows[900]["hw_status"] = "2"
        for r in rows[1200:]:
            r["_ts"] += 5000          # an outage gap, must not be filled
        X, summary = build_training_matrix(rows, allow_synthetic=True)
        self.assertGreaterEqual(summary["segments"], 4)
        self.assertEqual(X.shape[1], len(FEATURE_NAMES))

    def test_model_loads_and_predicts(self):
        meta, _ = train(syn_training_rows(), self.model_path, allow_synthetic=True)
        self.assertTrue(meta["trained_on_synthetic"])
        det, info = load_detector(self.model_path)
        self.assertTrue(info["ready"])
        normal_tail = [sample_from_values(T0 + i * STEP, a, b, 1, SYN)
                       for i, (a, b) in enumerate(syn_normal(30, seed=11))]
        odd_tail = [sample_from_values(T0 + i * STEP, a, b * 0.4, 1, SYN)
                    for i, (a, b) in enumerate(syn_normal(30, seed=12))]
        r_norm = det.evaluate(normal_tail)
        r_odd = det.evaluate(odd_tail)
        self.assertTrue(r_norm["evaluated"] and r_norm["trained"])
        self.assertFalse(r_norm["is_anomaly"])
        self.assertTrue(r_odd["is_anomaly"])
        self.assertTrue(r_odd["reasons"])          # an explanation is given

    def test_missing_and_corrupt_model_files_are_safe(self):
        det, info = load_detector(os.path.join(self.dir.name, "nope.joblib"))
        self.assertIsNone(det)
        self.assertFalse(info["ready"])
        bad = os.path.join(self.dir.name, "bad.joblib")
        with open(bad, "wb") as fh:
            fh.write(b"not a model")
        det, info = load_detector(bad)
        self.assertIsNone(det)
        self.assertFalse(info["ready"])

    def test_monitor_uses_trained_model_when_present(self):
        train(syn_training_rows(), self.model_path, allow_synthetic=True)
        m, clock = new_monitor(load_model=True, model_path=self.model_path)
        last = feed(syn_normal(100, seed=5), m)
        clock.t = last + 1
        p = m.analyse()
        self.assertTrue(p["model"]["ready"])
        self.assertTrue(p["model"]["trained_model_in_use"])
        self.assertEqual(p["model"]["method_in_use"], "isolation_forest")


class TestModelReload(unittest.TestCase):
    def test_running_monitor_picks_up_newly_trained_model(self):
        # SYNTHETIC model, test only.
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(config, "MIN_TRAIN_VALID_SECONDS", 600), \
                mock.patch.object(config, "MIN_TRAIN_WINDOWS", 500):
            mp = os.path.join(d, "m.joblib")
            m, clock = new_monitor(load_model=True, model_path=mp)
            clock.t = feed(syn_normal(100, seed=5), m) + 1
            self.assertFalse(m.analyse()["model"]["ready"])
            train(syn_training_rows(), mp, allow_synthetic=True)
            self.assertTrue(m.analyse()["model"]["ready"])


class TestLiveVsSyntheticSeparation(unittest.TestCase):        # item 13
    def test_live_monitor_rejects_synthetic(self):
        live = AIMonitor(allow_synthetic=False, load_model=False, now_fn=Clock(T0))
        self.assertFalse(live.ingest(sample_from_values(T0, 5, 5, 1, SYN)))
        p = live.analyse()
        self.assertEqual(p["mode"], "live")
        self.assertIsNone(p["sensors"]["sensor1"])
        self.assertTrue(any("Rejected" in w for w in p["warnings"]))

    def test_synthetic_monitor_is_labelled(self):
        p = run_scenario(syn_normal(100))
        self.assertEqual(p["mode"], "synthetic_test")
        self.assertEqual(p["source"], SYN)

    def test_live_monitor_accepts_live_label(self):
        live = AIMonitor(allow_synthetic=False, load_model=False, now_fn=Clock(T0))
        self.assertTrue(live.ingest(sample_from_values(T0, 5, 5, 1, config.SOURCE_LIVE)))


class TestLocalisation(unittest.TestCase):                     # item 14
    NODE = {"name": "Sector One", "lat": 4.843056, "lng": 7.013611}

    def test_unknown_layout_never_locates(self):
        for code in ("UNUSUAL_FLOW", "POTENTIAL_LEAK"):
            r = localise(code, {"imbalance": {"diff_shift": 2.0}}, self.NODE)
            self.assertEqual(r["status"], "unknown")
            self.assertEqual(r["message"], UNKNOWN_MESSAGE)
            self.assertIsNone(r["section"])
            self.assertIn("NOT the location of a leak", r["project_location"]["note"])

    def test_not_applicable_and_unavailable(self):
        self.assertEqual(localise("NORMAL", {}, None)["status"], "not_applicable")
        self.assertEqual(localise("DATA_QUALITY_ISSUE", {}, None)["status"], "unavailable")

    def test_verified_layout_gives_section_only(self):
        with mock.patch.object(config, "SENSOR_LAYOUT", config.LAYOUT_S1_UPSTREAM):
            r = localise("POTENTIAL_LEAK", {"imbalance": {"diff_shift": 1.5}}, self.NODE)
            self.assertEqual(r["status"], "section_estimate")
            self.assertEqual(r["section"], "between Sensor 1 and Sensor 2")
            self.assertNotIn("lat", str(r["section"]))
            r2 = localise("POTENTIAL_LEAK", {"imbalance": {"diff_shift": -1.5}}, self.NODE)
            self.assertEqual(r2["status"], "unknown")

    def test_payload_contains_unknown_location_message(self):
        p = run_scenario(syn_leak(150, 40))
        self.assertEqual(p["localisation"]["message"], UNKNOWN_MESSAGE)


class TestApiAndIntegration(unittest.TestCase):                # item 12
    """Plumbing test only. 'Raw Blynk text' below is SIMULATED to exercise the hooks."""

    def setUp(self):
        from flask import Flask
        self.dir = tempfile.TemporaryDirectory()
        self.patch_log = mock.patch.object(config, "LOG_PATH", os.path.join(self.dir.name, "l.csv"))
        self.patch_log.start()
        integration._monitor = None
        integration._collector = None
        integration._raw.clear()
        self.app = Flask(__name__)
        self.state = {"online": True}
        integration.register_ai(self.app, self.state, threading.RLock(),
                                {"name": "Sector One", "lat": 1.0, "lng": 2.0})
        self.client = self.app.test_client()

    def tearDown(self):
        self.patch_log.stop()
        integration._monitor = None
        integration._collector = None
        integration._raw.clear()
        self.dir.cleanup()

    def test_status_with_no_data_reports_unavailable(self):
        r = self.client.get("/api/ai/status")
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertEqual(j["mode"], "live")
        self.assertEqual(j["status"]["code"], "DATA_QUALITY_ISSUE")
        self.assertIsNone(j["sensors"]["sensor1"])
        self.assertIn("Nothing is being substituted", j["status"]["summary"])

    def test_hooks_feed_status_and_collector(self):
        integration.ai_on_raw("V0", "5.5")
        integration.ai_on_raw("V1", "5.3")
        integration.ai_on_poll(5.5, 5.3, 1)
        j = self.client.get("/api/ai/status").get_json()
        self.assertEqual(j["source"], "blynk_live")
        self.assertEqual(j["sensors"]["sensor1"]["value"], 5.5)
        self.assertTrue(j["sensors"]["fresh"])
        self.assertIs(j["backend_online"], True)
        d = self.client.get("/api/ai/data-collection").get_json()
        self.assertEqual(d["collector"]["rows_written_this_session"], 1)
        self.assertNotIn("token", str(d).lower())

    def test_unreadable_raw_value_is_not_zero_in_api(self):
        integration.ai_on_raw("V0", "garbage")
        integration.ai_on_raw("V1", "5.3")
        integration.ai_on_poll(0.0, 5.3, 1)       # the backend would have passed 0.0
        j = self.client.get("/api/ai/status").get_json()
        self.assertEqual(j["status"]["code"], "DATA_QUALITY_ISSUE")
        self.assertIsNone(j["sensors"]["sensor1"]["value"])
        self.assertEqual(j["sensors"]["sensor1"]["status"], "invalid")

    def test_without_raw_hook_warns_that_zero_may_be_unverified(self):
        integration.ai_on_poll(0.0, 0.0, 0)
        j = self.client.get("/api/ai/status").get_json()
        self.assertTrue(any("Raw-value checking is not active" in w for w in j["warnings"]))

    def test_internal_error_returns_safe_json_not_crash(self):
        with mock.patch.object(integration.get_monitor(), "analyse", side_effect=RuntimeError("boom")):
            r = self.client.get("/api/ai/status")
        self.assertEqual(r.status_code, 500)
        self.assertEqual(r.get_json()["error"], "RuntimeError")
        self.assertNotIn("Traceback", r.get_data(as_text=True))

    def test_hooks_never_raise(self):
        with mock.patch.object(integration, "get_monitor", side_effect=RuntimeError("x")):
            integration.ai_on_poll(1, 1, 1)      # must not raise
        integration.ai_on_raw(None, None)         # must not raise


class TestSyntheticComparison(unittest.TestCase):              # item 15
    """
    Compares the new assessment with a simple fixed-threshold baseline on SYNTHETIC series.
    Output shows PROTOTYPE behaviour only - not real-world performance.
    """

    def test_compare_against_threshold_baseline(self):
        onset, n_after = 150, 60
        normal_for_threshold = syn_normal(500, seed=99)
        diffs = np.array([a - b for a, b in normal_for_threshold])
        thr = float(abs(diffs.mean()) + 3 * diffs.std())      # SYNTHETIC-calibrated threshold

        def threshold_flag(a, b):
            return abs(a - b) > thr + abs(diffs.mean())

        series = []
        for s in range(10):
            series.append(("leak", onset, syn_leak(onset, n_after, drop=0.2, seed=100 + s)))
        for s in range(10):
            series.append(("normal", None, syn_normal(onset + n_after, seed=200 + s)))
        for s in range(10):
            series.append(("demand_change", onset, syn_common_mode(onset, n_after, seed=300 + s)))
        for s in range(10):
            series.append(("lag_transient", onset, syn_lag_transient(onset, seed=400 + s)))

        res = {"threshold": {"tp": 0, "fn": 0, "fp_series": 0, "delays": []},
               "assessment": {"tp": 0, "fn": 0, "fp_series": 0, "delays": []}}
        for kind, on, pairs in series:
            m, clock = new_monitor()
            ts = T0
            thr_first = ai_first = None
            for i, (a, b) in enumerate(pairs):
                m.ingest(sample_from_values(ts, a, b, 1, SYN))
                clock.t = ts + 1
                if i >= 80:
                    if thr_first is None and threshold_flag(a, b):
                        thr_first = i
                    if ai_first is None and m.analyse()["status"]["code"] == "POTENTIAL_LEAK":
                        ai_first = i
                ts += STEP
            for name, first in (("threshold", thr_first), ("assessment", ai_first)):
                if kind == "leak":
                    if first is not None and first >= on:
                        res[name]["tp"] += 1
                        res[name]["delays"].append(first - on)
                    else:
                        res[name]["fn"] += 1
                        if first is not None:
                            res[name]["fp_series"] += 1     # flagged before onset
                elif first is not None:
                    res[name]["fp_series"] += 1

        print("\n[SYNTHETIC DATA ONLY - prototype behaviour, not real-world performance]")
        print("10 leak, 10 normal, 10 demand-change and 10 lag-transient series (the last three are NOT leaks).")
        for name, r in res.items():
            tp, fn, fp = r["tp"], r["fn"], r["fp_series"]
            prec = tp / (tp + fp) if (tp + fp) else float("nan")
            rec = tp / (tp + fn) if (tp + fn) else float("nan")
            delay = float(np.mean(r["delays"])) if r["delays"] else float("nan")
            print("  %-10s precision=%.2f recall=%.2f false-positive series=%d "
                  "mean detection delay=%.1f readings (%.0f s)" %
                  (name, prec, rec, fp, delay, delay * STEP))
        self.assertEqual(len(series), 40)
        self.assertTrue(all(k in res for k in ("threshold", "assessment")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
