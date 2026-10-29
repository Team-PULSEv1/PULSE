
const API_URL = '/api/latest';
const NOTIF_URL = '/api/notifications';
const CONFIG_URL = '/api/config';
const DEFAULT_VIEW = 'map';


const BOOT_NODE = Object.freeze({
    id: 'SECTOR-1',
    name: 'Sector One',
    location: 'Port Harcourt, Rivers State, Nigeria',
    lat: 4.843056,
    lng: 7.013611,
    type: 'Pipeline monitoring sector',
    coordinates: 'configured'
});

// ===== SVG ICONS =====

const ICONS = {
    bell: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>',
    alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>',
    block: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',
    pressure: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>',
    close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
    location: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>'
};

// ===== LIVE DATA =====

let nodeConfig = null;

let liveData = {
    flow1: 0,
    flow2: 0,
    status: -1,
    countdown: 0,
    battery: null,
    batteryLive: false,
    batterySimulated: false,
    online: false,
    activeIncidents: []
};

let notifications = [];
let liveChart = null;
let comboChart = null;

let map = null;
let sectorMarker = null;
let sectorInfoWindow = null;

let markerBlinkTimer = null;
let markerBlinkOn = false;
let currentMarkerState = 'unavailable';

let pendingMapTarget = null;
let currentRange = '1h';

const MAX_SAMPLES = 2000;

let sampleBuf = {
    t: [],
    pressure: [],
    flow: [],
    avg: [],
    status: []
};

const reduceMotionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');

// ===== SAFE DOM HELPERS =====

function $(id) {
    return document.getElementById(id);
}

function setText(id, text) {
    const el = $(id);
    if (el) el.textContent = text;
}

function esc(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function fmtTime(ts) {
    const d = new Date(ts);
    return d.toTimeString().slice(0, 8) + ' · ' + d.toLocaleDateString();
}

function fmtClock(ts) {
    return new Date(ts).toTimeString().slice(0, 5);
}

function activeNode() {
    return nodeConfig || BOOT_NODE;
}

function nodeCenter() {
    const n = activeNode();
    return {
        lat: Number(n.lat),
        lng: Number(n.lng)
    };
}

// ===== DISPLAY MAPPING =====

function mapToDisplay(actualFlow) {
    const f = Number(actualFlow);

    if (f <= 0) return 0;
    if (f < 3) return Math.round((f / 3) * 25);
    if (f <= 5) return 25;
    if (f < 15) return Math.round(25 + ((f - 5) / 10) * 50);

    return 75;
}

// ===== INIT =====

document.addEventListener('DOMContentLoaded', function () {
    initialiseApp();
});

async function initialiseApp() {
    initCharts();
    bindNav();
    bindNotifActions();
    bindRangeButtons();
    bindMapTypeButtons();

    await fetchConfig();

    // The application always launches on the Map, including a fresh refresh.
    if (location.hash !== '#/' + DEFAULT_VIEW) {
        history.replaceState(null, '', '#/' + DEFAULT_VIEW);
    }

    applyRoute();
    startRealtimeUpdates();
}

async function fetchConfig() {
    try {
        const res = await fetch(CONFIG_URL);

        if (!res.ok) {
            throw new Error('HTTP ' + res.status);
        }

        nodeConfig = await res.json();
    } catch (err) {
        nodeConfig = BOOT_NODE;
    }

    if ($('view-nodes').classList.contains('active')) {
        renderNodes();
    }
}

// VIEW ROUTING

function bindNav() {
    document.querySelectorAll('[data-view]').forEach(function (el) {
        el.addEventListener('click', function () {
            showView(el.getAttribute('data-view'));
        });
    });

    $('btn-bell').addEventListener('click', function () {
        showView('history');
    });

    window.addEventListener('hashchange', applyRoute);
}

function applyRoute() {
    const v = (location.hash || '#/' + DEFAULT_VIEW)
        .replace('#/', '')
        .split('?')[0];

    showView(
        ['dashboard', 'map', 'nodes', 'history'].indexOf(v) >= 0
            ? v
            : DEFAULT_VIEW,
        true
    );
}

function showView(name, skipHash) {
    document.querySelectorAll('.view').forEach(function (v) {
        v.classList.remove('active');
    });

    const el = $('view-' + name);

    if (el) {
        el.classList.add('active');
    }

    document.querySelectorAll('.nav-tab').forEach(function (t) {
        t.classList.toggle(
            'active',
            t.getAttribute('data-view') === name
        );
    });

    if (!skipHash && location.hash !== '#/' + name) {
        location.hash = '#/' + name;
    }

    if (name === 'map') {
        initMapIfNeeded();

        if (map && window.google) {
            google.maps.event.trigger(map, 'resize');
            applyPendingMapTarget();
            updateSectorMarker();
        }
    }

    if (name === 'nodes') {
        renderNodes();
    }

    if (name === 'history') {
        renderHistory();
    }

    window.scrollTo(0, 0);
}

// NOTIFICATION STORE

function sevRank(sev) {
    return sev === 'critical' ? 0 : (sev === 'warning' ? 1 : 2);
}

function sevClass(sev) {
    return sev === 'critical'
        ? 'crit'
        : (sev === 'warning' ? 'warn' : 'info');
}

function typeTitle(n) {
    if (n.title) return n.title;

    const map = {
        leak: 'Leak Detected',
        blockage: 'Blockage Detected',
        pressure_surge: 'Pressure Surge'
    };

    return map[n.type] || 'Pipeline Alert';
}

function activeNotifications() {
    return notifications
        .filter(function (n) {
            return !n.acknowledged;
        })
        .sort(function (a, b) {
            return sevRank(a.severity) - sevRank(b.severity) || b.ts - a.ts;
        });
}

function updateBellBadge() {
    const n = activeNotifications().length;
    const badge = $('bell-badge');

    badge.textContent = n;
    badge.style.display = n > 0 ? 'block' : 'none';
}

function renderNotifCentre() {
    const box = $('notif-centre');

    if (!box) return;

    updateBellBadge();

    const act = activeNotifications();

    if (!act.length) {
        box.innerHTML =
            '<div class="empty-state">' +
                '<div class="empty-bell">' + ICONS.bell + '</div>' +
                '<h4>No new notifications</h4>' +
                '<p>You\'re all caught up!</p>' +
            '</div>';
        return;
    }

    box.innerHTML = notifCardHTML(act[0]);
}

function notifCardHTML(n) {
    const icon = n.severity === 'critical'
        ? ICONS.alert
        : (n.severity === 'warning' ? ICONS.block : ICONS.check);

    const loc = n.node_name ||
        n.location_desc ||
        (activeNode().name + ' · ' + activeNode().location);

    return '' +
        '<div class="notif-card ' + sevClass(n.severity) + '">' +
            '<div class="nc-head">' +
                '<span class="nc-icon">' + icon + '</span>' +
                '<div style="flex:1;min-width:0;">' +
                    '<div class="nc-title">' + esc(typeTitle(n)) + '</div>' +
                    '<div class="nc-meta">' + esc(fmtTime(n.ts)) + '</div>' +
                '</div>' +
                '<span class="sev-chip ' + sevClass(n.severity) + '">' +
                    esc(n.severity.toUpperCase()) +
                '</span>' +
            '</div>' +
            '<div class="nc-desc">' + esc(n.message || '') + '</div>' +
            '<div class="nc-loc">' +
                ICONS.location +
                '<span>' + esc(loc) + '</span>' +
            '</div>' +
            '<div class="nc-actions">' +
                '<button class="btn btn-primary" data-ack="' + n.id + '">' +
                    'Acknowledge' +
                '</button>' +
                '<button class="btn" data-map="' + n.id + '">' +
                    'View Location on Map' +
                '</button>' +
            '</div>' +
        '</div>';
}

function renderHistory() {
    const list = $('history-list');

    if (!list) return;

    if (!notifications.length) {
        list.innerHTML =
            '<div class="hist-empty">No notifications recorded yet.</div>';
        return;
    }

    list.innerHTML = notifications.map(function (n) {
        const loc = n.node_name ||
            n.location_desc ||
            (activeNode().name + ' · ' + activeNode().location);

        return '' +
            '<div class="hist-item ' + sevClass(n.severity) + '">' +
                '<div class="hist-head">' +
                    '<span class="sev-chip ' + sevClass(n.severity) + '">' +
                        esc(n.severity.toUpperCase()) +
                    '</span>' +
                    '<span class="hist-title">' + esc(typeTitle(n)) + '</span>' +
                    '<span class="ack-chip ' +
                        (n.acknowledged ? 'done">ACKNOWLEDGED' : 'pending">NEEDS ATTENTION') +
                    '</span>' +
                    '<span class="hist-time">' + esc(fmtTime(n.ts)) + '</span>' +
                '</div>' +
                '<div class="hist-desc">' + esc(n.message || '') + '</div>' +
                '<div class="hist-meta">' +
                    '<span>' + ICONS.location + ' ' + esc(loc) + '</span>' +
                    (n.node_id ? '<span>Node: ' + esc(n.node_id) + '</span>' : '') +
                    (
                        n.resolved
                            ? '<span>Incident: RESOLVED</span>'
                            : '<span>Incident: ACTIVE / UNRESOLVED</span>'
                    ) +
                '</div>' +
                '<div class="hist-actions">' +
                    '<button class="btn" data-map="' + n.id + '">' +
                        'View Location on Map' +
                    '</button>' +
                    (
                        !n.acknowledged
                            ? '<button class="btn btn-primary" data-ack="' + n.id + '">Acknowledge</button>'
                            : ''
                    ) +
                '</div>' +
            '</div>';
    }).join('');
}

function bindNotifActions() {
    ['notif-centre', 'history-list'].forEach(function (id) {
        const el = $(id);

        if (!el) return;

        el.addEventListener('click', function (e) {
            const ackBtn = e.target.closest('[data-ack]');

            if (ackBtn) {
                acknowledgeNotification(
                    ackBtn.getAttribute('data-ack')
                );
                return;
            }

            const mapBtn = e.target.closest('[data-map]');

            if (mapBtn) {
                openNotificationOnMap(
                    mapBtn.getAttribute('data-map')
                );
            }
        });
    });
}

async function fetchNotifications() {
    try {
        const res = await fetch(NOTIF_URL);

        if (!res.ok) {
            throw new Error('HTTP ' + res.status);
        }

        notifications = await res.json();
        renderNotifCentre();

        if ($('view-history').classList.contains('active')) {
            renderHistory();
        }
    } catch (err) {
        // Keep last known state.
    }
}

async function acknowledgeNotification(id) {
    try {
        await fetch(
            NOTIF_URL + '/' + encodeURIComponent(id) + '/acknowledge',
            { method: 'POST' }
        );

        await fetchNotifications();
    } catch (err) {
        // Retry on next poll.
    }
}

// NOTIFICATION MAP ACTIONS

function openNotificationOnMap(id) {
    const n = notifications.find(function (x) {
        return String(x.id) === String(id);
    });

    if (!n) return;

    const node = activeNode();

    pendingMapTarget = {
        notifId: n.id,
        title: typeTitle(n),
        severity: n.severity,
        message: n.message,
        ts: n.ts,
        nodeId: n.node_id || node.id,
        lat: n.lat != null ? n.lat : node.lat,
        lng: n.lng != null ? n.lng : node.lng,
        locationDesc:
            n.node_name ||
            n.location_desc ||
            (node.name + ' · ' + node.location),
        locationSource: n.location_source || 'configured-node'
    };

    showView('map');
}

function applyPendingMapTarget() {
    if (!pendingMapTarget || !map) return;

    const t = pendingMapTarget;

    map.setCenter({
        lat: Number(t.lat),
        lng: Number(t.lng)
    });

    map.setZoom(13);

    renderMapDetails(t);
    updateSectorMarker();

    pendingMapTarget = null;
}

function renderMapDetails(t) {
    const box = $('map-details');

    if (!box) return;

    const configured = t.locationSource !== 'gps';

    box.hidden = false;

    box.innerHTML =
        '<div class="md-head">' +
            '<div class="md-title">' +
                ICONS.alert +
                '<span>' + esc(t.title) + '</span>' +
            '</div>' +
            '<div style="display:flex;gap:8px;align-items:center;">' +
                '<span class="sev-chip ' + sevClass(t.severity) + '">' +
                    esc(String(t.severity).toUpperCase()) +
                '</span>' +
                '<span class="conf-badge ' + (configured ? '' : 'exact') + '">' +
                    (configured ? 'CONFIGURED NODE' : 'EXACT GPS') +
                '</span>' +
            '</div>' +
        '</div>' +
        '<div class="md-grid">' +
            '<div><strong>Node:</strong> ' + esc(t.nodeId || activeNode().id) + '</div>' +
            '<div><strong>Location:</strong> ' + esc(t.locationDesc) + '</div>' +
            '<div><strong>Description:</strong> ' + esc(t.message || '--') + '</div>' +
            '<div><strong>Detected:</strong> ' + esc(fmtTime(t.ts)) + '</div>' +
            (
                t.lat != null
                    ? '<div><strong>Coordinates:</strong> ' +
                      Number(t.lat).toFixed(4) + ', ' +
                      Number(t.lng).toFixed(4) + '</div>'
                    : ''
            ) +
            '<div><strong>Position source:</strong> ' +
                (
                    configured
                        ? 'configured sector location, not a live GPS fix'
                        : 'GPS fix'
                ) +
            '</div>' +
        '</div>' +
        '<div style="margin-top:12px;">' +
            '<button class="btn" data-view="dashboard">' +
                '← Back to Dashboard' +
            '</button>' +
        '</div>';

    box.querySelector('[data-view]').addEventListener('click', function () {
        showView('dashboard');
    });
}

// TOASTS


function sendToast(status) {
    const box = $('toast-container');
    const node = activeNode();

    const types = {
        0: {
            title: 'SYSTEM OKAY',
            msg: node.name + ' is idle - waiting for water flow',
            icon: ICONS.check,
            cls: 'success'
        },
        1: {
            title: 'NORMAL FLOW',
            msg: node.name + ' flow rate in normal range',
            icon: ICONS.check,
            cls: 'success'
        },
        2: {
            title: 'LEAK DETECTED',
            msg: 'Active incident associated with ' + node.id + ' / ' + node.name,
            icon: ICONS.alert,
            cls: 'alert'
        },
        3: {
            title: 'BLOCKAGE DETECTED',
            msg: 'Active incident associated with ' + node.id + ' / ' + node.name,
            icon: ICONS.block,
            cls: 'warning'
        },
        4: {
            title: 'PRESSURE SURGE',
            msg: 'Active incident associated with ' + node.id + ' / ' + node.name,
            icon: ICONS.pressure,
            cls: 'alert'
        }
    };

    const t = types[status];

    if (!t) return;

    const toast = document.createElement('div');
    toast.className = 'toast ' + t.cls;

    toast.innerHTML =
        '<div class="toast-icon">' + t.icon + '</div>' +
        '<div class="toast-content">' +
            '<div class="toast-title">' + t.title + '</div>' +
            '<div class="toast-message">' + t.msg + '</div>' +
        '</div>' +
        '<button class="toast-close" onclick="this.parentElement.remove()">' +
            ICONS.close +
        '</button>';

    box.appendChild(toast);

    setTimeout(function () {
        if (toast.parentElement) {
            toast.remove();
        }
    }, 6000);
}

// FETCH BACKEND DATA

async function fetchDashboardData() {
    try {
        const res = await fetch(API_URL);

        if (!res.ok) {
            throw new Error('HTTP ' + res.status);
        }

        const d = await res.json();

        liveData.flow1 = d.flow1;
        liveData.flow2 = d.flow2;
        liveData.status = d.status;
        liveData.countdown = d.countdown;
        liveData.battery =
            d.batterySim != null
                ? d.batterySim
                : (d.battery != null && d.batteryLive
                    ? d.battery
                    : null);
        liveData.batterySimulated = d.batterySim != null;
        liveData.batteryLive = !!d.batteryLive;
        liveData.online = d.online !== false;
        liveData.activeIncidents = Array.isArray(d.activeIncidents)
            ? d.activeIncidents
            : [];

        if (d.node && d.node.id) {
            nodeConfig = d.node;
        }

        if (d.statusChanged) {
            sendToast(d.status);
        }

        if (
            d.history &&
            d.history.flow1 &&
            d.history.flow1.length &&
            sampleBuf.t.length === 0
        ) {
            seedBuffer(d.history);
        }

        pushSample();

        updateCards();
        updateChartsLive();
        updateSectorMarker();

        const pill = $('online-pill');
        pill.classList.toggle('offline', !liveData.online);
        pill.innerHTML =
            '<span class="online-dot"></span> ' +
            (liveData.online ? 'Online' : 'Offline');

        const sb = $('system-status');

        if (sb) {
            sb.classList.toggle('offline', !liveData.online);
            sb.innerHTML =
                '<span class="dot"></span> ' +
                (liveData.online ? 'ACTIVE' : 'OFFLINE');
        }
    } catch (err) {
        liveData.online = false;

        const pill = $('online-pill');
        pill.classList.add('offline');
        pill.innerHTML =
            '<span class="online-dot"></span> OFFLINE';

        const sb = $('system-status');

        if (sb) {
            sb.classList.add('offline');
            sb.innerHTML =
                '<span class="dot"></span> OFFLINE';
        }

        updateSectorMarker();
    }
}


// TOP CARDS

function updateCards() {
    const f1 = liveData.flow1;
    const f2 = liveData.flow2;
    const avg = (f1 + f2) / 2;
    const dAvg = mapToDisplay(avg);

    const pressureVal = Math.min(
        120,
        Math.round(dAvg * 1.6)
    );

    const pressurePct = Math.min(
        100,
        Math.round((pressureVal / 120) * 100)
    );

    setText('val-pressure', pressureVal);
    setText(
        'val-pressure-percent',
        pressurePct + '% of operating range'
    );

    $('bar-pressure').style.width = pressurePct + '%';

    const flowVal = Math.round(dAvg * 8);

    const flowPct = Math.min(
        100,
        Math.round((dAvg / 75) * 100)
    );

    setText('val-flow', flowVal);
    setText('val-flow-percent', flowPct + '% throughput');

    $('bar-flow').style.width = flowPct + '%';

    const card = $('card-battery');

    if (liveData.battery != null) {
        card.classList.remove('unavailable');

        const b = Math.max(
            0,
            Math.min(100, Math.round(liveData.battery))
        );

        setText('val-battery', b);
        setText(
            'val-battery-note',
            b > 20
                ? (liveData.batterySimulated
                    ? 'reading'
                    : 'Telemetry link healthy')
                : (liveData.batterySimulated
                    ? 'reading'
                    : 'Low charge - schedule maintenance')
        );

        $('bar-battery').style.width = b + '%';
    } else {
        card.classList.add('unavailable');

        setText('val-battery', '--');
        setText(
            'val-battery-note',
            liveData.online
                ? 'No battery telemetry from node'
                : 'Telemetry unavailable'
        );

        $('bar-battery').style.width = '0%';
    }
}


// CHARTS

function chartDefaults() {
    Chart.defaults.color = '#8fb3ad';
    Chart.defaults.font.size = 10;
    Chart.defaults.borderColor = 'rgba(46,226,192,0.08)';
}

function initCharts() {
    chartDefaults();

    const lc = $('liveChart');

    liveChart = new Chart(lc.getContext('2d'), {
        type: 'line',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'Pressure (PSI)',
                    data: [],
                    yAxisID: 'y',
                    borderColor: '#2ee2c0',
                    borderWidth: 2,
                    tension: 0.4,
                    pointRadius: 0,
                    pointHoverRadius: 4,
                    fill: true,
                    backgroundColor: 'rgba(46,226,192,0.10)'
                },
                {
                    label: 'Flow (L/min)',
                    data: [],
                    yAxisID: 'y1',
                    borderColor: '#38bdf8',
                    borderWidth: 2,
                    tension: 0.4,
                    pointRadius: 0,
                    pointHoverRadius: 4,
                    fill: true,
                    backgroundColor: 'rgba(56,189,248,0.08)'
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                mode: 'index',
                intersect: false
            },
            plugins: {
                legend: {
                    display: false
                },
                tooltip: {
                    backgroundColor: 'rgba(3,13,19,0.95)',
                    borderColor: 'rgba(46,226,192,0.4)',
                    borderWidth: 1,
                    padding: 10,
                    cornerRadius: 6,
                    callbacks: {
                        title: function (items) {
                            if (!items.length) return '';

                            const idx = items[0].dataIndex;

                            return sampleBuf.t[idx]
                                ? fmtClock(sampleBuf.t[idx])
                                : '';
                        }
                    }
                }
            },
            scales: {
                x: {
                    grid: {
                        display: false
                    },
                    ticks: {
                        maxTicksLimit: 6,
                        callback: function (v) {
                            return fmtClock(
                                this.getLabelForValue(v)
                            );
                        }
                    }
                },
                y: {
                    min: 0,
                    max: 120,
                    position: 'left',
                    title: {
                        display: true,
                        text: 'PSI',
                        color: '#2ee2c0',
                        font: {
                            size: 9
                        }
                    }
                },
                y1: {
                    min: 0,
                    max: 640,
                    position: 'right',
                    grid: {
                        drawOnChartArea: false
                    },
                    title: {
                        display: true,
                        text: 'L/min',
                        color: '#38bdf8',
                        font: {
                            size: 9
                        }
                    }
                }
            }
        }
    });

    const cc = $('comboChart');

    comboChart = new Chart(cc.getContext('2d'), {
        data: {
            labels: [],
            datasets: [
                {
                    type: 'bar',
                    label: 'Pressure',
                    data: [],
                    order: 2,
                    backgroundColor: [],
                    borderRadius: 3,
                    barPercentage: 0.7,
                    categoryPercentage: 0.8
                },
                {
                    type: 'line',
                    label: 'Trend',
                    data: [],
                    order: 1,
                    borderColor: '#8fd8ff',
                    borderWidth: 2,
                    tension: 0.45,
                    pointRadius: 0,
                    fill: false
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: false
                },
                tooltip: {
                    backgroundColor: 'rgba(3,13,19,0.95)',
                    borderColor: 'rgba(46,226,192,0.4)',
                    borderWidth: 1,
                    padding: 10,
                    cornerRadius: 6
                }
            },
            scales: {
                x: {
                    grid: {
                        display: false
                    },
                    ticks: {
                        maxTicksLimit: 6,
                        callback: function (v) {
                            return fmtClock(
                                this.getLabelForValue(v)
                            );
                        }
                    }
                },
                y: {
                    min: 0,
                    max: 100,
                    ticks: {
                        callback: function (v) {
                            return v + '%';
                        },
                        stepSize: 25
                    }
                }
            }
        }
    });

    renderLiveChart();
    renderComboChart();
}

function bindRangeButtons() {
    $('range-group').addEventListener('click', function (e) {
        const btn = e.target.closest('.range-btn');

        if (!btn) return;

        currentRange = btn.getAttribute('data-range');

        document.querySelectorAll('.range-btn').forEach(function (b) {
            b.classList.toggle('active', b === btn);
        });

        renderLiveChart();
    });
}

function rangeSampleCount() {
    if (currentRange === '1h') {
        return Math.min(1800, sampleBuf.t.length);
    }

    if (currentRange === '6h') {
        return Math.min(2000, sampleBuf.t.length);
    }

    return sampleBuf.t.length;
}

function seedBuffer(h) {
    const times = h.times || [];

    for (let i = 0; i < h.flow1.length; i++) {
        const t = times[i] ||
            (Date.now() - (h.flow1.length - i) * 2000);

        const avg = (h.flow1[i] + h.flow2[i]) / 2;

        sampleBuf.t.push(t);
        sampleBuf.pressure.push(
            Math.min(120, Math.round(mapToDisplay(avg) * 1.6))
        );
        sampleBuf.flow.push(
            Math.round(mapToDisplay(avg) * 8)
        );
        sampleBuf.avg.push(mapToDisplay(avg));
        sampleBuf.status.push(1);
    }
}

function pushSample() {
    const avg = (liveData.flow1 + liveData.flow2) / 2;
    const dAvg = mapToDisplay(avg);

    sampleBuf.t.push(Date.now());
    sampleBuf.pressure.push(
        Math.min(120, Math.round(dAvg * 1.6))
    );
    sampleBuf.flow.push(Math.round(dAvg * 8));
    sampleBuf.avg.push(dAvg);
    sampleBuf.status.push(liveData.status);

    if (sampleBuf.t.length > MAX_SAMPLES) {
        ['t', 'pressure', 'flow', 'avg', 'status'].forEach(function (k) {
            sampleBuf[k].shift();
        });
    }
}

function renderLiveChart() {
    if (!liveChart) return;

    const n = rangeSampleCount();
    const off = sampleBuf.t.length - n;

    liveChart.data.labels = sampleBuf.t.slice(off);
    liveChart.data.datasets[0].data = sampleBuf.pressure.slice(off);
    liveChart.data.datasets[1].data = sampleBuf.flow.slice(off);

    liveChart.update('none');
}

function renderComboChart() {
    if (!comboChart) return;

    const n = 14;
    const total = sampleBuf.t.length;
    const off = Math.max(0, total - n);

    const labels = sampleBuf.t.slice(off);
    const vals = sampleBuf.avg.slice(off);
    const stats = sampleBuf.status.slice(off);

    comboChart.data.labels = labels;
    comboChart.data.datasets[0].data = vals;
    comboChart.data.datasets[0].backgroundColor = vals.map(function (v, i) {
        return stats[i] === 4
            ? '#ffd60a'
            : 'rgba(46,226,192,0.75)';
    });

    comboChart.data.datasets[1].data = vals.map(function (_, i) {
        const s = vals.slice(Math.max(0, i - 4), i + 1);

        return Math.round(
            s.reduce(function (a, b) {
                return a + b;
            }, 0) / s.length
        );
    });

    comboChart.update('none');
}

function updateChartsLive() {
    renderLiveChart();
    renderComboChart();
}

// GOOGLE MAP - EXACTLY ONE SECTOR ONE MARKER

function initMapIfNeeded() {
    if (map || !window.google || !google.maps) return;

    const mapDiv = $('map');

    if (!mapDiv || !mapDiv.clientWidth) return;

    initMap();
}

function initMap() {
    const mapDiv = $('map');

    if (!mapDiv || !mapDiv.clientWidth) return;

    map = new google.maps.Map(mapDiv, {
        zoom: 13,
        center: nodeCenter(),
        mapTypeId: 'satellite',
        mapTypeControl: false,
        streetViewControl: false,
        fullscreenControl: true,
        zoomControl: true,
        zoomControlOptions: {
            position: google.maps.ControlPosition.RIGHT_CENTER
        }
    });

    createSectorMarker();
    updateSectorMarker();
    applyPendingMapTarget();
}

function createSectorMarker() {
    if (sectorMarker) return;

    sectorMarker = new google.maps.Marker({
        position: nodeCenter(),
        map: map,
        title: activeNode().id + ' / ' + activeNode().name,
        icon: markerIcon('unavailable', false)
    });

    sectorInfoWindow = new google.maps.InfoWindow({
        content: markerInfoContent()
    });

    sectorMarker.addListener('click', function () {
        sectorInfoWindow.setContent(markerInfoContent());
        sectorInfoWindow.open(map, sectorMarker);
    });
}

function hasActiveIncident() {
    return (
        Array.isArray(liveData.activeIncidents) &&
        liveData.activeIncidents.length > 0
    );
}

function getMarkerVisualState() {
    const alertStatus =
        liveData.status === 2 ||
        liveData.status === 3 ||
        liveData.status === 4;

    if (alertStatus || hasActiveIncident()) {
        return 'alert';
    }

    if (!liveData.online) {
        return 'unavailable';
    }

    if (liveData.status === 1) {
        return 'normal';
    }

    return 'unknown';
}

function markerIcon(state, blinkOn) {
    const styles = {
        normal: {
            fill: '#22e58a',
            stroke: '#dffff4',
            fillOpacity: 1.0,
            strokeOpacity: 1.0,
            scale: 12,
            strokeWeight: 2
        },
        alert: {
            fill: '#ff4d5e',
            stroke: '#ffd7dc',
            fillOpacity: blinkOn ? 1.0 : 0.22,
            strokeOpacity: blinkOn ? 1.0 : 0.35,
            scale: 13,
            strokeWeight: reduceMotionQuery.matches ? 4 : 2
        },
        unknown: {
            fill: '#ffab3d',
            stroke: '#ffe3ad',
            fillOpacity: 0.95,
            strokeOpacity: 1.0,
            scale: 12,
            strokeWeight: 2
        },
        unavailable: {
            fill: '#52636a',
            stroke: '#9bb8b3',
            fillOpacity: 0.78,
            strokeOpacity: 0.85,
            scale: 12,
            strokeWeight: 2
        }
    };

    const s = styles[state] || styles.unknown;

    return {
        path: google.maps.SymbolPath.CIRCLE,
        fillColor: s.fill,
        fillOpacity: s.fillOpacity,
        strokeColor: s.stroke,
        strokeOpacity: s.strokeOpacity,
        strokeWeight: s.strokeWeight,
        scale: s.scale
    };
}

function stopMarkerBlink() {
    if (markerBlinkTimer) {
        clearInterval(markerBlinkTimer);
        markerBlinkTimer = null;
    }

    markerBlinkOn = false;
}

function updateSectorMarker() {
    if (!sectorMarker || !window.google || !google.maps) {
        return;
    }

    const next = getMarkerVisualState();

    if (next !== currentMarkerState) {
        currentMarkerState = next;
        stopMarkerBlink();

        sectorMarker.setIcon(
            markerIcon(next, false)
        );

        if (next === 'alert' && !reduceMotionQuery.matches) {
            markerBlinkTimer = setInterval(function () {
                markerBlinkOn = !markerBlinkOn;

                sectorMarker.setIcon(
                    markerIcon('alert', markerBlinkOn)
                );
            }, 560);
        }
    }

    sectorMarker.setPosition(nodeCenter());

    if (sectorInfoWindow) {
        sectorInfoWindow.setContent(
            markerInfoContent()
        );
    }
}

function markerStatusLabel() {
    const state = getMarkerVisualState();

    if (state === 'normal') {
        return 'NORMAL OPERATION';
    }

    if (state === 'alert') {
        return 'ACTIVE PIPELINE ALERT';
    }

    if (!liveData.online) {
        return 'TELEMETRY UNAVAILABLE';
    }

    return 'STANDBY / UNCONFIRMED';
}

function markerInfoContent() {
    const node = activeNode();

    const active = hasActiveIncident()
        ? liveData.activeIncidents.map(function (i) {
            return esc(i.title || i.type || 'incident');
        }).join(', ')
        : 'None recorded';

    const state = getMarkerVisualState();

    const color =
        state === 'normal'
            ? '#22e58a'
            : (
                state === 'alert'
                    ? '#ff4d5e'
                    : '#ffab3d'
            );

    return '' +
        '<div style="padding:8px;min-width:230px;">' +
            '<h4 style="margin:0 0 5px 0;font-size:13px;">' +
                esc(node.id) + ' / ' + esc(node.name) +
            '</h4>' +
            '<div style="color:' + color + ';font-weight:700;font-size:11px;margin-bottom:5px;">' +
                markerStatusLabel() +
            '</div>' +
            '<p style="margin:0 0 5px;font-size:10px;color:#d9fff6;">' +
                esc(node.location) +
            '</p>' +
            '<p style="margin:0 0 5px;font-size:10px;color:#8fb3ad;">' +
                'Configured: ' +
                Number(node.lat).toFixed(4) + ', ' +
                Number(node.lng).toFixed(4) +
                ' (not a live GPS fix)' +
            '</p>' +
            '<p style="margin:0;font-size:10px;color:#8fb3ad;">' +
                'Active incidents: ' + active +
            '</p>' +
        '</div>';
}

// NODES PAGE


function renderNodes() {
    const list = $('nodes-list');

    if (!list) return;

    const n = activeNode();
    const online = liveData.online;

    list.innerHTML =
        '<div class="node-card">' +
            '<div class="node-head">' +
                '<span class="node-dot ' + (online ? '' : 'offline') + '"></span>' +
                '<div>' +
                    '<div class="node-name">' + esc(n.name) + '</div>' +
                    '<div class="node-id">ID: ' + esc(n.id) + '</div>' +
                '</div>' +
            '</div>' +
            '<div class="node-rows">' +
                '<div>Location: <strong>' + esc(n.location) + '</strong></div>' +
                '<div>Status: <strong>' + (online ? 'ONLINE' : 'OFFLINE') + '</strong></div>' +
                '<div>Marker state: <strong>' + esc(markerStatusLabel()) + '</strong></div>' +
                '<div>Last communication: <strong>' + (online ? 'just now' : 'no link') + '</strong></div>' +
                (
                    liveData.battery != null
                        ? '<div>Battery: <strong>' + Math.round(liveData.battery) + '%</strong></div>'
                        : ''
                ) +
                '<div>Coordinates: <strong>' +
                    Number(n.lat).toFixed(4) + ', ' +
                    Number(n.lng).toFixed(4) +
                '</strong> (' + esc(n.coordinates) + ')</div>' +
            '</div>' +
            '<div style="display:flex;gap:8px;flex-wrap:wrap;">' +
                '<button class="btn" data-node-detail="' + esc(n.id) + '">Details</button>' +
                '<button class="btn btn-primary" data-node-map="' + esc(n.id) + '">View on Map</button>' +
            '</div>' +
        '</div>';

    list.querySelectorAll('[data-node-detail]').forEach(function (b) {
        b.addEventListener('click', function () {
            showNodeDetail(
                b.getAttribute('data-node-detail')
            );
        });
    });

    list.querySelectorAll('[data-node-map]').forEach(function (b) {
        b.addEventListener('click', function () {
            focusNodeOnMap();
        });
    });
}

function showNodeDetail(id) {
    const n = activeNode();

    if (String(id) !== String(n.id)) return;

    const box = $('node-detail');

    if (!box) return;

    box.hidden = false;

    box.innerHTML =
        '<div class="page-title">' + esc(n.name) + '</div>' +
        '<div class="page-sub">' +
            esc(n.type) + ' · ' + esc(n.location) +
        '</div>' +
        '<div class="md-grid">' +
            '<div><strong>Node ID:</strong> ' + esc(n.id) + '</div>' +
            '<div><strong>Connection:</strong> ' + (liveData.online ? 'ONLINE' : 'OFFLINE') + '</div>' +
            '<div><strong>Marker state:</strong> ' + esc(markerStatusLabel()) + '</div>' +
            '<div><strong>Coordinates:</strong> ' +
                Number(n.lat).toFixed(5) + ', ' +
                Number(n.lng).toFixed(5) +
                ' (' + esc(n.coordinates) + ')' +
            '</div>' +
            (
                liveData.battery != null
                    ? '<div><strong>Battery:</strong> ' + Math.round(liveData.battery) + '%</div>'
                    : ''
            ) +
            '<div><strong>Flow 1:</strong> ' + mapToDisplay(liveData.flow1) + ' mL/S</div>' +
            '<div><strong>Flow 2:</strong> ' + mapToDisplay(liveData.flow2) + ' mL/S</div>' +
            '<div><strong>Active incidents:</strong> ' +
                (hasActiveIncident() ? liveData.activeIncidents.length : 0) +
            '</div>' +
        '</div>';

    box.scrollIntoView({
        behavior: 'smooth',
        block: 'nearest'
    });
}

function focusNodeOnMap() {
    const n = activeNode();

    pendingMapTarget = {
        notifId: null,
        title: n.id + ' / ' + n.name,
        severity: 'info',
        message: 'Registered monitoring node at the configured Port Harcourt sector location.',
        ts: Date.now(),
        nodeId: n.id,
        lat: n.lat,
        lng: n.lng,
        locationDesc: n.name + ' · ' + n.location,
        locationSource: 'configured-node'
    };

    showView('map');
}

// REALTIME

function startRealtimeUpdates() {
    fetchDashboardData();
    fetchNotifications();

    setInterval(fetchDashboardData, 2000);
    setInterval(fetchNotifications, 4000);
}

function bindMapTypeButtons() {
    const bm = $('btn-map');
    const bs = $('btn-sat');

    if (bm) {
        bm.addEventListener('click', function () {
            if (map) {
                map.setMapTypeId('roadmap');
            }

            bm.classList.add('active');
            bs.classList.remove('active');
        });
    }

    if (bs) {
        bs.addEventListener('click', function () {
            if (map) {
                map.setMapTypeId('satellite');
            }

            bs.classList.add('active');
            bm.classList.remove('active');
        });
    }
}


window.initMap = initMap;