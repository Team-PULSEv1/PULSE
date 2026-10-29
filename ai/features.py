"""
ai/features.py  (NEW FILE)
Turns a window of readings into numbers the detector can use.
Units are unknown, so all features are in the sensors' own units.
"""
import numpy as np

FEATURE_NAMES = ["f1_last", "f2_last", "diff_last",
                 "f1_mean", "f2_mean", "diff_mean",
                 "f1_std", "f2_std", "diff_std",
                 "f1_slope", "f2_slope"]


def _slope(w):
    """Least-squares slope per reading for each row of w (shape n x W)."""
    n = w.shape[1]
    x = np.arange(n, dtype=float) - (n - 1) / 2.0
    return (w * x).sum(axis=1) / (x ** 2).sum()


def window_features_matrix(w1, w2):
    """w1, w2: arrays of shape (n_windows, WINDOW). Returns (n_windows, 11)."""
    w1 = np.asarray(w1, dtype=float)
    w2 = np.asarray(w2, dtype=float)
    d = w1 - w2
    cols = [w1[:, -1], w2[:, -1], d[:, -1],
            w1.mean(axis=1), w2.mean(axis=1), d.mean(axis=1),
            w1.std(axis=1), w2.std(axis=1), d.std(axis=1),
            _slope(w1), _slope(w2)]
    return np.column_stack(cols)


def window_features(f1_values, f2_values):
    """Features for ONE window (two equal-length lists). Returns a list of 11 floats."""
    if len(f1_values) != len(f2_values) or len(f1_values) < 2:
        raise ValueError("need two equal-length windows with at least 2 readings")
    m = window_features_matrix([f1_values], [f2_values])
    return [float(v) for v in m[0]]
