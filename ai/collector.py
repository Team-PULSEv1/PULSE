"""
ai/collector.py  (NEW FILE)
Writes REAL sensor readings to a local CSV file for future training/evaluation.

- Never stores tokens or credentials (only the fixed columns below).
- Refuses anything not labelled as live data (keeps synthetic data out).
- Skips unchanged duplicates, but writes a heartbeat row every HEARTBEAT_S so
  quiet periods are still recorded. Each row has since_last_row_s so a trainer
  can rebuild the regular 2-second timeline without inventing data.
- Never raises: a disk problem is recorded in status()['last_error'].
- Freshness: a row is only written right after a successful poll, so rows are
  fresh by construction; gaps between rows reveal outages.
"""
import csv
import os
import threading
import time
from datetime import datetime, timezone

from . import config

FIELDS = ["ts_epoch", "ts_utc", "source", "sensor1", "sensor2",
          "sensor1_status", "sensor2_status", "valid", "hw_status",
          "raw_verified", "since_last_row_s", "row_reason"]


class RealDataCollector:
    def __init__(self, path=None, heartbeat_s=None, allow_synthetic=False):
        self.path = path or config.LOG_PATH
        self.heartbeat_s = config.HEARTBEAT_S if heartbeat_s is None else heartbeat_s
        self.allow_synthetic = allow_synthetic
        self._lock = threading.Lock()
        self._last_key = None
        self._last_write_ts = None
        self.rows_written = 0
        self.rows_skipped_duplicate = 0
        self.rows_rejected = 0
        self.last_error = None

    @staticmethod
    def _key(s):
        return (s["f1"], s["f2"], s["f1_status"], s["f2_status"],
                s["hw_status"], s["raw_verified"])

    def add(self, sample):
        """Maybe write one row. Returns the reason written, or None if skipped."""
        try:
            with self._lock:
                if sample["source"] != config.SOURCE_LIVE and not (
                        self.allow_synthetic and sample["source"] == config.SOURCE_SYNTHETIC):
                    self.rows_rejected += 1
                    return None
                ts = sample["ts"]
                key = self._key(sample)
                if self._last_key is None:
                    reason = "first"
                elif key != self._last_key:
                    reason = "change"
                elif ts - self._last_write_ts >= self.heartbeat_s:
                    reason = "heartbeat"
                else:
                    self.rows_skipped_duplicate += 1
                    return None

                d = os.path.dirname(self.path)
                if d:
                    os.makedirs(d, exist_ok=True)
                new_file = (not os.path.exists(self.path)) or os.path.getsize(self.path) == 0
                since = "" if self._last_write_ts is None else round(ts - self._last_write_ts, 3)
                valid = int(sample["f1_status"] in ("valid_zero", "valid_nonzero")
                            and sample["f2_status"] in ("valid_zero", "valid_nonzero"))
                row = {
                    "ts_epoch": round(ts, 3),
                    "ts_utc": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                    "source": sample["source"],
                    "sensor1": "" if sample["f1"] is None else sample["f1"],
                    "sensor2": "" if sample["f2"] is None else sample["f2"],
                    "sensor1_status": sample["f1_status"],
                    "sensor2_status": sample["f2_status"],
                    "valid": valid,
                    "hw_status": "" if sample["hw_status"] is None else sample["hw_status"],
                    "raw_verified": int(sample["raw_verified"]),
                    "since_last_row_s": since,
                    "row_reason": reason,
                }
                with open(self.path, "a", newline="", encoding="utf-8") as fh:
                    w = csv.DictWriter(fh, fieldnames=FIELDS)
                    if new_file:
                        w.writeheader()
                    w.writerow(row)
                self._last_key = key
                self._last_write_ts = ts
                self.rows_written += 1
                self.last_error = None
                return reason
        except Exception as exc:          # never break the polling thread
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            return None

    def status(self):
        size = None
        try:
            size = os.path.getsize(self.path)
        except OSError:
            pass
        return {"path_name": os.path.basename(self.path),
                "rows_written_this_session": self.rows_written,
                "duplicates_skipped": self.rows_skipped_duplicate,
                "rows_rejected_not_live": self.rows_rejected,
                "file_size_bytes": size,
                "last_write_ts": self._last_write_ts,
                "heartbeat_s": self.heartbeat_s,
                "last_error": self.last_error}
