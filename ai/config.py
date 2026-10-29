"""
ai/config.py  (NEW FILE)
------------------------------------------------------------------
Every tunable setting for the AI extension lives here.

IMPORTANT - READ THIS:
Every number below is a SOFTWARE DEFAULT chosen so the code can run.
None of them is a measured property of your pipeline or sensors.
Your sensor units, calibration, normal operating range and accuracy are
UNKNOWN to this code. Tune these only after you have collected real data
(see INTEGRATION.md, "Collecting and using real data").
"""
import os

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(_PKG_DIR)          # the folder holding pulse_server.py

# ---- where files go -------------------------------------------------
DATA_DIR = os.environ.get("PULSE_AI_DATA_DIR", os.path.join(PROJECT_ROOT, "ai_data"))
LOG_PATH = os.path.join(DATA_DIR, "real_readings.csv")      # REAL readings only
MODEL_PATH = os.environ.get("PULSE_AI_MODEL_PATH",
                            os.path.join(_PKG_DIR, "models", "iforest.joblib"))

# ---- data-source labels (keeps real and synthetic data apart) -------
SOURCE_LIVE = "blynk_live"
SOURCE_SYNTHETIC = "synthetic_test"     # used by automated tests ONLY

# ---- polling / freshness -------------------------------------------
POLL_INTERVAL_S = 2.0        # matches POLL_INTERVAL_S in pulse_server.py
STALE_AFTER_S = 10.0         # newest reading older than this = STALE
MAX_GAP_S = STALE_AFTER_S    # a time gap bigger than this breaks a "continuous" run
BUFFER_LEN = 600             # readings kept in memory (about 20 min at 2 s)
ALLOW_NEGATIVE = False       # negative flow is treated as INVALID (an assumption!)

# ---- windows and the "recent history" baseline ---------------------
WINDOW = 10                  # readings per feature window (about 20 s)
BASELINE_MIN_HISTORY = 60    # earlier readings needed before comparing (about 2 min)
BASELINE_Z = 5.0             # how many "robust spreads" away counts as unusual
BASELINE_REL_FLOOR = 0.02    # spread is never smaller than 2 % of the flow level
BASELINE_ABS_FLOOR = 1e-6

# ---- potential-leak evidence ---------------------------------------
STEADY_REL_STD = 0.10        # flow counts as "steady" if its spread <= 10 % of its mean
PERSIST_EVALS = 5            # imbalance must hold for this many consecutive evaluations
CONST_NONZERO_RUN = 30       # identical NONZERO readings in a row -> "suspiciously constant"

# ---- real-data logger ----------------------------------------------
HEARTBEAT_S = 10.0           # write at least one row this often, even if nothing changed

# ---- model training -------------------------------------------------
TRAIN_ALLOWED_HW_STATUS = (0, 1)         # only train on rows the hardware called idle/normal
TRAIN_MAX_FILL_GAP_S = 3 * HEARTBEAT_S   # bigger gap in the log = data outage, never filled
MIN_TRAIN_VALID_SECONDS = 4 * 3600       # refuse to train on less than 4 h of valid data
MIN_TRAIN_WINDOWS = 1000
MIN_NONZERO_SHARE = 0.05                 # refuse if almost no flowing data was collected
CONTAMINATION = 0.01                     # Isolation Forest: share flagged on TRAINING-like data

# ---- localisation ---------------------------------------------------
# Leave as "unknown" until YOU have physically verified the sensor layout.
SENSOR_LAYOUT = os.environ.get("PULSE_SENSOR_LAYOUT", "unknown")
LAYOUT_S1_UPSTREAM = "s1_upstream_of_s2_same_pipe"   # the only verified layout supported

LIMITATIONS = [
    "Sensor units, calibration, accuracy and normal operating range are unknown.",
    "Physical positions of Sensor 1 and Sensor 2 are unverified.",
    "Results are prototype assessments, not confirmed leak detection.",
]
