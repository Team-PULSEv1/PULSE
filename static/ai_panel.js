// ============================================================
// PULSE AI panel (NEW FILE: static/ai_panel.js)
// Adds an "AI Monitoring" panel to the Dashboard without editing dashboard.js.
// Data source: GET /api/ai/status (real Blynk readings only, never simulated).
// Reuses existing classes: panel, panel-head, sev-chip, conf-badge, md-grid.
// ============================================================
(function () {
    'use strict';

    var AI_URL = '/api/ai/status';
    var REFRESH_MS = 4000;

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function chipClass(code) {
        if (code === 'POTENTIAL_LEAK') return 'crit';
        if (code === 'NORMAL') return 'info';
        return 'warn';
    }

    function fmtVal(s) {
        if (!s) return '--';
        if (s.value == null) return 'no valid value (' + esc(s.status) + ')';
        var label = s.status === 'valid_zero' ? ' (valid zero)' : '';
        return esc(Number(s.value).toPrecision(5).replace(/\.?0+$/, '')) + label;
    }

    function fmtTs(ts) {
        if (ts == null) return '--';
        var d = new Date(ts * 1000);
        return d.toTimeString().slice(0, 8) + ' · ' + d.toLocaleDateString();
    }

    function injectStyle() {
        if (document.getElementById('ai-panel-style')) return;
        var st = document.createElement('style');
        st.id = 'ai-panel-style';
        st.textContent =
            '#ai-panel{grid-column:1/-1;padding-bottom:16px;border-color:var(--line-strong);}' +
            '#ai-panel .ai-head-right{display:flex;gap:8px;align-items:center;flex-wrap:wrap;}' +
            '#ai-panel .ai-summary{padding:6px 18px 0;font-size:13px;color:var(--text);}' +
            '#ai-panel .ai-cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));' +
            'gap:14px 24px;padding:12px 18px 0;}' +
            '#ai-panel .ai-col h4{font-size:12px;font-weight:700;color:var(--teal);margin-bottom:6px;}' +
            '#ai-panel .ai-col div,#ai-panel .ai-col li{font-size:12px;color:var(--muted);line-height:1.5;}' +
            '#ai-panel .ai-col strong{color:var(--text);font-weight:600;}' +
            '#ai-panel .ai-col ul{padding-left:16px;}' +
            '#ai-panel .ai-warn{color:var(--orange);}' +
            '#ai-panel .ai-foot{padding:10px 18px 0;font-size:11px;color:var(--muted);}';
        document.head.appendChild(st);
    }

    function buildPanel() {
        var host = document.querySelector('#view-dashboard .charts-grid') ||
                   document.getElementById('view-dashboard');
        if (!host || document.getElementById('ai-panel')) return null;
        var el = document.createElement('div');
        el.className = 'panel';
        el.id = 'ai-panel';
        el.innerHTML =
            '<div class="panel-head">' +
                '<h3><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
                '<path d="M12 2v4M12 18v4M4.9 4.9l2.8 2.8M16.3 16.3l2.8 2.8M2 12h4M18 12h4' +
                'M4.9 19.1l2.8-2.8M16.3 7.7l2.8-2.8"/><circle cx="12" cy="12" r="3"/></svg>' +
                'AI Monitoring</h3>' +
                '<div class="ai-head-right" id="ai-chips"></div>' +
            '</div>' +
            '<div class="ai-summary" id="ai-summary">Loading AI status…</div>' +
            '<div class="ai-cols" id="ai-cols"></div>' +
            '<div class="ai-foot" id="ai-foot"></div>';
        host.appendChild(el);
        return el;
    }

    function render(d) {
        var st = d.status || {};
        var sens = d.sensors || {};
        var model = d.model || {};
        var hist = d.history || {};
        var loc = d.localisation || {};

        var chips = '<span class="sev-chip ' + chipClass(st.code) + '">' +
                    esc(st.label || 'UNKNOWN') + '</span>';
        if (!model.ready) {
            chips += '<span class="conf-badge">MODEL NOT READY</span>';
        }
        document.getElementById('ai-chips').innerHTML = chips;
        document.getElementById('ai-summary').textContent = st.summary || '';

        var freshTxt = sens.timestamp == null
            ? 'no reading received'
            : (sens.fresh ? 'fresh' : 'STALE') +
              (sens.age_s != null ? ' (' + Math.round(sens.age_s) + ' s old)' : '');
        var validTxt = sens.timestamp == null ? '--' : (sens.valid ? 'valid' : 'NOT valid');

        var sensorsHtml =
            '<div class="ai-col"><h4>Real sensor readings</h4>' +
            '<div><strong>Sensor 1:</strong> ' + fmtVal(sens.sensor1) + '</div>' +
            '<div><strong>Sensor 2:</strong> ' + fmtVal(sens.sensor2) + '</div>' +
            '<div><strong>Units:</strong> unverified</div>' +
            '<div><strong>Latest reading:</strong> ' + esc(fmtTs(sens.timestamp)) + '</div>' +
            '<div><strong>Freshness:</strong> ' + esc(freshTxt) + '</div>' +
            '<div><strong>Validity:</strong> ' + esc(validTxt) + '</div>' +
            '<div><strong>Hardware status code:</strong> ' +
                (d.hardware_status_code == null ? '--' : esc(d.hardware_status_code)) +
                ' (existing logic, separate from AI)</div>' +
            '</div>';

        var expl = (d.explanation || []).map(function (t) {
            return '<li>' + esc(t) + '</li>';
        }).join('');
        var warns = (d.warnings || []).map(function (t) {
            return '<li class="ai-warn">' + esc(t) + '</li>';
        }).join('');
        var aiHtml =
            '<div class="ai-col"><h4>AI analysis</h4>' +
            '<div><strong>Method:</strong> ' +
                esc((d.anomaly && d.anomaly.method) || '--') +
                ((d.anomaly && d.anomaly.trained) ? '' : ' (baseline, not a trained model)') + '</div>' +
            '<div><strong>Enough history:</strong> ' + (hist.sufficient ? 'yes' : 'no') +
                ' (' + esc(hist.contiguous_valid_readings) + ' / ' +
                esc(hist.needed_for_baseline) + ' readings)</div>' +
            '<div><strong>Trained model ready:</strong> ' + (model.ready ? 'yes' : 'no') + '</div>' +
            (model.ready ? '' : '<div>' + esc(model.message || '') + '</div>') +
            '<ul>' + expl + warns + '</ul></div>';

        var proj = loc.project_location;
        var locHtml =
            '<div class="ai-col"><h4>Localisation</h4>' +
            '<div><strong>Status:</strong> ' + esc(loc.status || '--') + '</div>' +
            '<div>' + esc(loc.message || '') + '</div>' +
            (proj ? '<div><strong>Project location:</strong> ' + esc(proj.name) +
                    ' (' + Number(proj.lat).toFixed(4) + ', ' + Number(proj.lng).toFixed(4) +
                    ') - fixed marker, not a leak location.</div>' : '') +
            '</div>';

        document.getElementById('ai-cols').innerHTML = sensorsHtml + aiHtml + locHtml;
        document.getElementById('ai-foot').textContent =
            (d.limitations || []).join(' ') + ' Mode: ' + (d.mode || '--') + '.';
    }

    function renderError(msg) {
        var chips = document.getElementById('ai-chips');
        var sum = document.getElementById('ai-summary');
        if (chips) chips.innerHTML = '<span class="sev-chip warn">DATA QUALITY ISSUE</span>';
        if (sum) sum.textContent = msg;
    }

    async function refresh() {
        try {
            var res = await fetch(AI_URL);
            var data = await res.json();
            if (!res.ok && !data.sensors) {
                renderError('AI analysis is unavailable (server error).');
                return;
            }
            render(data);
        } catch (e) {
            renderError('Cannot reach the AI endpoint. Existing dashboard features are unaffected.');
        }
    }

    function start() {
        injectStyle();
        if (!buildPanel()) return;
        refresh();
        setInterval(refresh, REFRESH_MS);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
})();
