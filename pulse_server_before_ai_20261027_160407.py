# """
# PULSE - Python Dashboard Backend (Flask)
# ========================================
# Serves the static dashboard and a clean JSON API.

# What this backend owns:
#   - ONE server-side poller for ALL browser tabs (fixes Blynk free-tier
#     rate-limit exhaustion)
#   - State-change detection -> one-shot `statusChanged` flag
#   - The NOTIFICATION STORE: the single source of truth shared by the
#     dashboard Notification Centre, bell badge and History view.
#     Notifications are created once per transition into an alert state and
#     acknowledgement does NOT resolve the underlying incident.
#   - The ACTIVE INCIDENT STORE used by the single map marker. Incidents remain
#     active until normal operation is reliably confirmed (status 1), or until
#     explicitly cleared through /api/incidents/<id>/clear.
#   - Optional battery telemetry (V4). If the hardware has no battery pin,
#     `battery` is null and the dashboard marks the card unavailable instead
#     of showing fabricated values.
#   - A SIMULATED battery model (`batterySim`): 100% when the device first
#     comes online, -1 point every 180 s while online, frozen on disconnect,
#     clamped to [0, 100]. It is deliberately kept separate from the real
#     V4 hardware reading and must not be presented as a measured value.
#   - The SECTOR-1 node configuration consumed by the frontend.

# Setup:
#     pip install flask requests
#     python pulse_server.py
# Then open http://localhost:5000

# Put index.html / style.css / dashboard.js / background.png in ./static/
# """

import os
from flask import Flask, jsonify, send_from_directory
import requests
import threading
import time
import json
import itertools
from concurrent.futures import ThreadPoolExecutor

# =========================
# CONFIG
# =========================
BLYNK_TOKEN = os.environ.get(
    'BLYNK_TOKEN',
    'gNYLH1vkdNRUBdVpUy0XVQT_NEOp0IFG'  # local fallback
)
BLYNK_API = 'https://blynk.cloud/external/api'
POLL_INTERVAL_S = 2.0
HISTORY_LEN = 20
NOTIF_LIMIT = 200

PINS = {'flow1': 'V0', 'flow2': 'V1', 'status': 'V2', 'countdown': 'V3'}
BATTERY_PIN = 'V4'

NODE = {
    'id': 'SECTOR-1',
    'name': 'Sector One',
    'location': 'Port Harcourt, Rivers State, Nigeria',
    'lat': 4.843056,
    'lng': 7.013611,
    'type': 'Pipeline monitoring sector',
    'coordinates': 'configured',
}

app = Flask(__name__, static_folder='static', static_url_path='')

state = {
    'flow1': 0.0,
    'flow2': 0.0,
    'status': 0,
    'countdown': 0,
    'battery': None,
    'batteryLive': False,
    'statusChanged': False,
    'history': {'flow1': [], 'flow2': [], 'avg': [], 'times': []},
    'online': False,
    'lastPollMs': 0,
    # Simulated battery model (NOT a hardware measurement):
    # 100% on confirmed connection, -1 percentage point every 180 s
    # while online, frozen when offline, never below 0%.
    'batterySim': None,
    'batterySimStart': None,
}

lock = threading.RLock()
_prev_status = None

_notif_ids = itertools.count(1)
notifications = []
active_incidents = []


def map_to_display(actual_flow):
    f = float(actual_flow)
    if f <= 0:
        return 0
    if f < 3:
        return round((f / 3) * 25)
    if f <= 5:
        return 25
    if f < 15:
        return round(25 + ((f - 5) / 10) * 50)
    return 75


def estimate_location(status, f1, f2):
    if status == 2:
        reason = (
            'Sensor 2 reads ~0 while Sensor 1 still flows - fluid is '
            'being lost at the monitored Sector One node.'
            if f2 < 1 else
            'Flow differential (F1 - F2) detected at the monitored '
            'Sector One node.'
        )
        return {'title': 'Sector One monitoring node', 'reason': reason}

    if status == 3:
        if f1 > 0 and f2 < 1:
            return {
                'title': 'Sector One monitoring node',
                'reason': 'Sensor 1 still flows but Sensor 2 does not - '
                          'the line appears blocked at the monitored '
                          'Sector One node.'
            }
        return {
            'title': 'Sector One monitoring node',
            'reason': 'Neither sensor registers flow - the blockage is '
                      'associated with the monitored Sector One node.'
        }

    if status == 4:
        return {
            'title': 'Sector One monitoring node',
            'reason': 'Pressure surge detected at intake - source is '
                      'associated with the monitored Sector One node.'
        }

    return None


def create_notification(status, f1, f2):
    types = {
        2: ('leak', 'critical', 'Leak Detected'),
        3: ('blockage', 'warning', 'Blockage Detected'),
        4: ('pressure_surge', 'critical', 'Pressure Surge'),
    }

    if status not in types:
        return

    ntype, severity, title = types[status]
    est = estimate_location(status, f1, f2) or {}
    rec_id = next(_notif_ids)

    rec = {
        'id': rec_id,
        'type': ntype,
        'severity': severity,
        'title': title,
        'message': est.get('reason', 'Pipeline alert condition detected.'),
        'ts': int(time.time() * 1000),
        'node_id': NODE['id'],
        'node_name': NODE['name'],
        'lat': NODE['lat'],
        'lng': NODE['lng'],
        'location_source': 'configured-node',
        'location_desc': est.get('title'),
        'acknowledged': False,
        'resolved': False,
    }

    notifications.insert(0, rec)
    del notifications[NOTIF_LIMIT:]

    if not any(i['type'] == ntype for i in active_incidents):
        active_incidents.insert(0, {
            'id': rec_id,
            'type': ntype,
            'severity': severity,
            'title': title,
            'message': rec['message'],
            'node_id': NODE['id'],
            'node_name': NODE['name'],
            'lat': NODE['lat'],
            'lng': NODE['lng'],
            'location_source': 'configured-node',
            'started_ts': rec['ts'],
        })


def resolve_active_incidents():
    if not active_incidents:
        return

    resolved_ids = {i['id'] for i in active_incidents}

    for rec in notifications:
        if rec['id'] in resolved_ids:
            rec['resolved'] = True

    active_incidents.clear()


def parse_blynk_value(resp):
    raw = None

    try:
        j = resp.json()
        if isinstance(j, list) and j:
            raw = j[0]
    except (ValueError, json.JSONDecodeError):
        raw = resp.text

    if raw is None:
        raw = resp.text

    s = str(raw).strip().strip('"').strip("'").strip('[]').strip()

    try:
        return float(s)
    except ValueError:
        return 0.0


def read_pin(pin):
    r = requests.get(
        f'{BLYNK_API}/get',
        params={'token': BLYNK_TOKEN, 'pin': pin},
        timeout=5,
    )
    r.raise_for_status()
    return parse_blynk_value(r)


def poll_blynk():
    global _prev_status

    pool = ThreadPoolExecutor(max_workers=5)

    while True:
        started = time.time()

        try:
            futures = {
                k: pool.submit(read_pin, p)
                for k, p in PINS.items()
            }

            battery_future = pool.submit(read_pin, BATTERY_PIN)

            f1 = futures['flow1'].result()
            f2 = futures['flow2'].result()
            st = int(futures['status'].result())
            cd = int(futures['countdown'].result())

            try:
                battery = battery_future.result(timeout=6)
                battery_live = True
            except Exception:
                battery = None
                battery_live = False

            with lock:
                was_online = state['online']
                now = time.time()

                # Battery simulation: (re)start at 100% the moment the
                # hardware connection is confirmed. No separate timer
                # thread - elapsed time is derived from the start
                # timestamp, so reconnects can never stack timers.
                if not was_online:
                    state['batterySim'] = 100
                    state['batterySimStart'] = now

                # Countdown runs only while online; on failure below the
                # level simply stops being recomputed (frozen).
                if state['batterySimStart'] is not None:
                    elapsed = now - state['batterySimStart']
                    state['batterySim'] = max(
                        0, 100 - int(elapsed // 180)
                    )

                state['flow1'] = f1
                state['flow2'] = f2
                state['status'] = st
                state['countdown'] = cd
                state['battery'] = battery
                state['batteryLive'] = battery_live
                state['online'] = True
                state['lastPollMs'] = int((time.time() - started) * 1000)

                changed = (
                    _prev_status is not None
                    and st != _prev_status
                )
                state['statusChanged'] = changed

                if changed and st in (2, 3, 4):
                    create_notification(st, f1, f2)

                if changed and st == 1:
                    resolve_active_incidents()

                _prev_status = st

                h = state['history']
                h['flow1'].append(f1)
                h['flow2'].append(f2)
                h['avg'].append((f1 + f2) / 2)
                h['times'].append(int(time.time() * 1000))

                for k in h:
                    if len(h[k]) > HISTORY_LEN:
                        h[k].pop(0)

        except Exception:
            with lock:
                state['online'] = False

        time.sleep(POLL_INTERVAL_S)


@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/api/config')
def api_config():
    with lock:
        return jsonify(dict(NODE))


@app.route('/api/latest')
def api_latest():
    with lock:
        payload = {
            'flow1': state['flow1'],
            'flow2': state['flow2'],
            'status': state['status'],
            'countdown': state['countdown'],
            'battery': state['battery'],
            'batteryLive': state['batteryLive'],
            'batterySim': state['batterySim'],
            'statusChanged': state['statusChanged'],
            'history': {
                k: list(v)
                for k, v in state['history'].items()
            },
            'online': state['online'],
            'responseMs': state['lastPollMs'],
            'node': dict(NODE),
            'activeIncidents': [
                dict(i)
                for i in active_incidents
            ],
        }

        state['statusChanged'] = False

    return jsonify(payload)


@app.route('/api/notifications')
def api_notifications():
    with lock:
        return jsonify([
            dict(n)
            for n in notifications
        ])


@app.route('/api/notifications/<int:nid>/acknowledge', methods=['POST'])
def api_acknowledge(nid):
    with lock:
        for rec in notifications:
            if rec['id'] == nid:
                rec['acknowledged'] = True
                return jsonify({
                    'ok': True,
                    'notification': dict(rec)
                })

    return jsonify({
        'ok': False,
        'error': 'not found'
    }), 404


@app.route('/api/incidents')
def api_incidents():
    with lock:
        return jsonify([
            dict(i)
            for i in active_incidents
        ])


@app.route('/api/incidents/<int:iid>/clear', methods=['POST'])
def api_clear_incident(iid):
    with lock:
        incident = next(
            (
                i for i in active_incidents
                if i['id'] == iid
            ),
            None
        )

        if not incident:
            return jsonify({
                'ok': False,
                'error': 'not found'
            }), 404

        active_incidents[:] = [
            i for i in active_incidents
            if i['id'] != iid
        ]

        for rec in notifications:
            if rec['id'] == iid:
                rec['resolved'] = True

        return jsonify({
            'ok': True,
            'incident': dict(incident)
        })


if __name__ == '__main__':
    t = threading.Thread(target=poll_blynk, daemon=True)
    t.start()
    app.run(
        host='0.0.0.0',
        port=int(os.environ.get('PORT', 5000)),
        threaded=True
    )