"""
ai/train_model.py  (NEW FILE)
Trains the Isolation Forest from the REAL log written by the collector.
Run it yourself, from the project folder:   python -m ai.train_model

Safety rules built in:
- Only rows with source 'blynk_live' are used (synthetic rows are refused).
- Only rows where BOTH sensors were valid and the hardware status was in
  TRAIN_ALLOWED_HW_STATUS are used.
- Log gaps (outages) are never filled in; they split the data into segments.
- It refuses to train with too little data, or almost no flowing data.
Unlabelled "normal" data cannot prove leak-detection performance.
"""
import argparse
import csv
import hashlib
import os
import sys
from datetime import datetime, timezone

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from . import config
from .features import FEATURE_NAMES, window_features_matrix


class TrainingError(Exception):
    pass


def read_log(path):
    rows, bad = [], 0
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                r["_ts"] = float(r["ts_epoch"])
                rows.append(r)
            except (KeyError, ValueError, TypeError):
                bad += 1
    rows.sort(key=lambda r: r["_ts"])
    return rows, bad


def build_training_matrix(rows, allow_synthetic=False):
    """Return (X, summary). Raises TrainingError if the data is not suitable."""
    ok_sources = {config.SOURCE_LIVE}
    if allow_synthetic:
        ok_sources.add(config.SOURCE_SYNTHETIC)
    segments = []
    a_list, b_list = [], []
    prev = None

    def flush():
        nonlocal a_list, b_list, prev
        if prev is not None:
            a_list.append(prev[1])
            b_list.append(prev[2])
        if a_list:
            segments.append((np.array(a_list), np.array(b_list)))
        a_list, b_list, prev = [], [], None

    used = 0
    for r in rows:
        try:
            good = (r.get("source") in ok_sources and r.get("valid") == "1"
                    and int(float(r.get("hw_status", ""))) in config.TRAIN_ALLOWED_HW_STATUS)
        except ValueError:
            good = False
        if not good:
            flush()
            continue
        used += 1
        t, a, b = r["_ts"], float(r["sensor1"]), float(r["sensor2"])
        if prev is not None:
            if t - prev[0] > config.TRAIN_MAX_FILL_GAP_S:
                flush()
            else:
                k = 0
                while prev[0] + k * config.POLL_INTERVAL_S < t - 1e-9:
                    a_list.append(prev[1])
                    b_list.append(prev[2])
                    k += 1
        prev = (t, a, b)
    flush()

    W = config.WINDOW
    mats, points, nonzero_pts = [], 0, 0
    for a, b in segments:
        points += len(a)
        nonzero_pts += int(np.sum((a > 0) | (b > 0)))
        if len(a) >= W:
            mats.append(window_features_matrix(sliding_window_view(a, W),
                                               sliding_window_view(b, W)))
    summary = {"rows_total": len(rows), "rows_used": used,
               "segments": len(segments),
               "valid_seconds": points * config.POLL_INTERVAL_S,
               "nonzero_share": (nonzero_pts / points) if points else 0.0,
               "n_windows": int(sum(m.shape[0] for m in mats))}
    if not mats:
        raise TrainingError("No usable continuous data found in the log.")
    X = np.vstack(mats)
    if summary["valid_seconds"] < config.MIN_TRAIN_VALID_SECONDS or \
            summary["n_windows"] < config.MIN_TRAIN_WINDOWS:
        raise TrainingError(
            "Not enough valid data: %.1f h available, %.1f h required "
            "(and at least %d windows; have %d)." % (
                summary["valid_seconds"] / 3600.0, config.MIN_TRAIN_VALID_SECONDS / 3600.0,
                config.MIN_TRAIN_WINDOWS, summary["n_windows"]))
    if summary["nonzero_share"] < config.MIN_NONZERO_SHARE:
        raise TrainingError(
            "Only %.1f %% of the data contains flow. A model trained on almost-only zero "
            "flow would flag normal operation. Collect data while the system is flowing."
            % (100 * summary["nonzero_share"]))
    return X, summary


def train(rows, out_path, contamination=None, allow_synthetic=False, source_file=None):
    from sklearn.ensemble import IsolationForest
    import joblib
    cont = config.CONTAMINATION if contamination is None else contamination
    X, summary = build_training_matrix(rows, allow_synthetic=allow_synthetic)
    model = IsolationForest(n_estimators=200, contamination=cont,
                            random_state=0, n_jobs=1).fit(X)
    med = np.median(X, axis=0)
    mad = np.median(np.abs(X - med), axis=0)
    scale = np.maximum(1.4826 * mad, 1e-9)
    sha = None
    if source_file and os.path.exists(source_file):
        h = hashlib.sha256()
        with open(source_file, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        sha = h.hexdigest()
    meta = {"feature_names": FEATURE_NAMES, "window": config.WINDOW,
            "trained_at_utc": datetime.now(timezone.utc).isoformat(),
            "n_train_windows": summary["n_windows"],
            "valid_seconds": summary["valid_seconds"],
            "nonzero_share": summary["nonzero_share"],
            "contamination": cont,
            "feature_median": med.tolist(), "feature_scale": scale.tolist(),
            "source_log_sha256": sha,
            "trained_on_synthetic": bool(allow_synthetic)}
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    joblib.dump({"model": model, "meta": meta}, out_path)
    return meta, summary


def main(argv=None):
    p = argparse.ArgumentParser(description="Train the PULSE Isolation Forest on REAL logged data.")
    p.add_argument("--log", default=config.LOG_PATH)
    p.add_argument("--out", default=config.MODEL_PATH)
    p.add_argument("--contamination", type=float, default=config.CONTAMINATION)
    args = p.parse_args(argv)
    if not os.path.exists(args.log):
        print("No log file at %s. Run the app for a while first." % args.log)
        return 1
    rows, bad = read_log(args.log)
    try:
        meta, summary = train(rows, args.out, args.contamination, source_file=args.log)
    except TrainingError as exc:
        print("NOT TRAINED: %s" % exc)
        return 2
    print("Trained on %d windows (%.1f h of valid data, %.0f %% flowing)." % (
        meta["n_train_windows"], meta["valid_seconds"] / 3600.0, 100 * meta["nonzero_share"]))
    print("Saved to %s" % args.out)
    print("Reminder: this learned what YOUR logged data looked like. It is not proof of "
          "leak-detection accuracy. Restart the app (or wait) to load the model.")
    if bad:
        print("Skipped %d malformed log rows." % bad)
    return 0


if __name__ == "__main__":
    sys.exit(main())
