# PULSE AI extension: integration guide

Your existing files are untouched. Everything here is **new files** plus **5 one-line additions**.

## 1. What goes where

Put the folders next to `pulse_server.py`:

```
your-project/
├── pulse_server.py        (existing, +3 lines)
├── requirements.txt       (existing, +2 lines)
├── static/
│   ├── index.html         (existing, +1 line)
│   ├── dashboard.js       (existing, NOT changed)
│   ├── style.css          (existing, NOT changed)
│   └── ai_panel.js        NEW
├── ai/                    NEW folder (10 .py files)
├── tests/test_ai.py       NEW
└── ai_data/               created automatically when logging starts
```

New files and what each does:

| File | Purpose |
|---|---|
| `ai/config.py` | All settings in one place. Every number is a software default, not a measured fact. |
| `ai/validation.py` | Valid zero / valid nonzero / missing / invalid / stale / constant checks. |
| `ai/features.py` | Turns 10 readings into 11 numbers (means, spreads, slopes, difference). |
| `ai/anomaly.py` | Recent-history **baseline** (not trained) and the Isolation Forest wrapper. |
| `ai/leak_assessment.py` | Decides between normal / unusual / potential leak / insufficient evidence / data quality. |
| `ai/localisation.py` | Says "location unknown" unless a verified layout allows a section-level answer. |
| `ai/monitor.py` | Keeps recent validated readings and builds the status result. |
| `ai/collector.py` | Writes real readings to `ai_data/real_readings.csv`. |
| `ai/train_model.py` | Trains the Isolation Forest from the real log (you run it). |
| `ai/integration.py` | The only file `pulse_server.py` talks to. Adds `/api/ai/status` and `/api/ai/data-collection`. |
| `static/ai_panel.js` | Adds the AI panel to the Dashboard. |

## 2. Install

Add these two lines to `requirements.txt`, then install in the **same virtual environment** you already use for Flask:

```
numpy
scikit-learn
```

```
pip install numpy scikit-learn
```

- `numpy`: maths on the readings. `scikit-learn`: the Isolation Forest (it also brings `joblib`, used to save the model).
- You do **not** need `pytest`; the tests use Python's built-in `unittest`.
- I tested with Python 3.12, Flask 3.1, numpy 2.4 and scikit-learn 1.8. I have not tested your Python version, so run the tests (section 5) after installing.

## 3. The five additions (exact)

**Addition 1: `pulse_server.py`, import**
Find: `from concurrent.futures import ThreadPoolExecutor`
Add on the next line:
```python
from ai.integration import register_ai, ai_on_raw, ai_on_poll
```
Why: loads the three functions the server needs.

**Addition 2: `pulse_server.py`, add the routes**
Find: `active_incidents = []` (top level, near `notifications = []`)
Add on the next line:
```python
register_ai(app, state, lock, NODE)
```
Why: registers `/api/ai/status` and `/api/ai/data-collection`. It must come after `app`, `state`, `lock` and `NODE` exist, which this spot guarantees.

**Addition 3: `pulse_server.py`, keep the raw Blynk text**
Find, inside `def read_pin(pin):`, the last line: `    return parse_blynk_value(r)`
Add the new line **before** it (same 4-space indent):
```python
    ai_on_raw(pin, r.text)
    return parse_blynk_value(r)
```
Why: `parse_blynk_value()` turns unreadable text into `0.0`, which is indistinguishable from a real zero. This line lets the AI see the raw text so it can tell them apart. It makes no extra Blynk request and does not change what `read_pin` returns.

**Addition 4: `pulse_server.py`, feed the AI once per poll**
Find these lines inside `poll_blynk()`:
```python
                for k in h:
                    if len(h[k]) > HISTORY_LEN:
                        h[k].pop(0)
```
Add **after** them, indented 12 spaces (the same level as the `with lock:` line above them):
```python
            ai_on_poll(f1, f2, st)
```
Why: reuses your single existing poll. It feeds the AI buffer and the logger. It cannot raise, so it cannot break polling.

**Addition 5: `static/index.html`**
Find: `<script src="dashboard.js"></script>`
Add on the next line:
```html
    <script src="ai_panel.js"></script>
```
Why: loads the panel, which adds itself to the Dashboard's chart grid.

I applied these five additions to a **scratch copy** of your uploaded files and confirmed each find-string occurs exactly once and the diff is exactly those 5 lines. If you skip Addition 3, everything still works, but the panel shows a warning that raw-value checking is inactive.

## 4. Run it and confirm it sees real Blynk data

```
python pulse_server.py
```
Open `http://localhost:5000`, go to the Dashboard and scroll to **AI Monitoring**.

Confirm real data is flowing:
1. The panel's **Mode** line (bottom) says `live`, and **Latest reading** timestamp advances every few seconds.
2. Sensor 1 and Sensor 2 values match your hardware/Blynk app. The panel shows no units because units are unverified.
3. Open `http://localhost:5000/api/ai/status` directly: `"source": "blynk_live"`.
4. Unplug the device or block Blynk: within about 15 s the panel changes to **DATA QUALITY ISSUE** (stale). It never invents values.

The status chip meanings:
- **NORMAL**: no unusual change versus the recent past. It does **not** mean flow is within a proper operating range (that range is unknown).
- **ANOMALY DETECTED**: unusual flow behaviour, no leak evidence.
- **POTENTIAL LEAK**: a persistent, steady shift in (Sensor 1 − Sensor 2) with supporting checks. It needs investigation and is **not** a confirmed leak.
- **INSUFFICIENT EVIDENCE**: not enough history yet, or a stuck sensor cannot be ruled out.
- **DATA QUALITY ISSUE**: no data, stale, missing or invalid reading.
- **MODEL NOT READY** (orange badge): no trained model yet, so the baseline is being used.

For the first ~2.5 minutes after starting the app you will see INSUFFICIENT EVIDENCE while history builds.

## 5. Tests

From the folder that contains `pulse_server.py`:
```
python -m unittest discover -s tests -v
```
What I actually ran (in my sandbox, Python 3.12): **51 tests, all passed**. They cover: validation (valid zero, missing, invalid, stale, constant); feature maths; insufficient history; anomaly vs leak separation (common-mode change, single spike, lagging sensor, stuck sensor are **not** called leaks); the logger (timestamps, duplicates, heartbeat, refusing synthetic rows, no credentials); training refusals; model load/predict and corrupt-file safety; live-vs-synthetic separation; localisation limits; API responses and error safety.

All test data is **synthetic** and labelled `synthetic_test`. Comparison against a simple fixed-threshold baseline, synthetic data, 10 series per case (printed by the last test):

| Method | Precision | Recall | False-positive series | Mean detection delay |
|---|---|---|---|---|
| Fixed threshold on \|S1−S2\| | 0.50 | 1.00 | 10 | 0 readings |
| New leak assessment | 1.00 | 1.00 | 0 | 11 readings (about 22 s) |

Read this carefully: I designed the "lag transient" case (Sensor 2 follows a step 6 readings late) because it is a known weakness of simple thresholds, so the threshold's false positives come from that case. Without it, the threshold was just as accurate and faster. These numbers show **prototype behaviour on synthetic data only**, not real-world performance.

## 6. Collecting and using real data

The collector runs automatically once the additions are in. It writes `ai_data/real_readings.csv` (no tokens or credentials), one row on any change plus a heartbeat row every 10 s. Columns include timestamp, both readings, validity status per sensor, the hardware status code, and `since_last_row_s`.

To collect useful data:
1. Leave the app running through normal operation, including idle and flowing periods, over days if you can.
2. Do not collect "normal" data while a known fault or test is happening.
3. Keep notes of any real events (planned shutdowns, real leaks, sensor maintenance) with times. Without these labels you cannot measure detection accuracy.

Then:
```
python -m ai.train_model
```
It refuses to train unless there are at least 4 hours of valid data containing real flow, and it never trains on invalid rows, synthetic rows, or periods where the hardware status was 2, 3 or 4. The app loads the new model automatically (no restart needed). The panel then shows "Trained model ready: yes".

What the collected data can and cannot do:
- It lets you understand how your flow normally behaves, choose better settings in `ai/config.py`, train the detector, and count false alarms during periods you know were normal.
- Unlabelled normal data **cannot prove** the system detects real leaks. For that you need real or properly staged leak events with recorded times.

## 7. Details I could not establish (marked unknown)

| Unknown | How you can verify it |
|---|---|
| Sensor units (cards say L/min, Nodes page says mL/S) | Check the sensor datasheet and the Arduino/firmware code that writes V0/V1. |
| Calibration and accuracy | Run both sensors on the same known flow; compare readings against a measured volume. |
| Normal operating range | Review the collected CSV over normal operation. |
| Which sensor is upstream, and whether they are on the same pipe | Physically inspect the installation. |
| What hardware status codes mean on the device | Check the firmware logic that writes V2. The AI is independent of it. |

Localisation stays "unknown" until you verify the layout. If Sensor 1 is upstream of Sensor 2 on the same pipe, set the environment variable `PULSE_SENSOR_LAYOUT=s1_upstream_of_s2_same_pipe`. Even then the answer is only "between Sensor 1 and Sensor 2", never coordinates. Locating a leak along a pipe would need more instrumentation (several flow or pressure sensors at known positions).

## 8. Limitations and things to know

- **Baseline forgets.** It detects changes, not abnormal levels. A problem lasting longer than the history in memory (about 20 min) becomes the new reference and stops being flagged. The trained model does not have this weakness.
- **Existing zero-conversion left as is.** The charts and cards still show unreadable values as 0 because I did not change `parse_blynk_value()`. Only the AI side sees the difference. Fixing the charts would need a change inside that existing function.
- **Pressure is not measured.** The dashboard's pressure is calculated from flow in `dashboard.js`, so the AI ignores it.
- **AI is separate from alerts.** It does not create notifications, change the map marker, or modify the hardware status. Wiring a potential-leak result into notifications is a deliberate later decision.
- **One server process.** The AI's recent readings are in memory, so run a single `python pulse_server.py` (as you do now). They reset when the app restarts.
- **Not tested here:** the real Blynk service and your hardware (no internet in my sandbox), a browser (the panel was only syntax-checked and run against a fake page with real AI payloads; please look at it yourself), and your Python version.
- **Secrets:** your Blynk token and Google Maps key are written in plain text in `pulse_server.py` and `index.html`. Rotate the token and restrict the Maps key before sharing the code. The AI code never logs or exposes either.
- **Model files:** only load model files you created yourself (they use Python's pickle format).
- **Defaults:** thresholds in `ai/config.py` (for example `BASELINE_Z`, `STEADY_REL_STD`, `PERSIST_EVALS`) are untuned starting points.

## 9. Suggested next steps

1. Apply the additions, run the tests, then run the app and check the panel against your Blynk readings.
2. Collect at least several days of real data and note real events.
3. Train, then review false alarms against your notes before trusting any result.
4. Verify the sensor layout and units, then update `ai/config.py`.
5. Only after validation, consider linking potential-leak results to notifications.
