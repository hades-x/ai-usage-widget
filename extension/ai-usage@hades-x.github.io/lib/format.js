// -*- mode: js; js-indent-level: 4; indent-tabs-mode: nil -*-
// Pure helpers: NO imports.*, NO GObject. Runs both inside gnome-shell (GJS 1.74)
// and under plain node (extension/tests/run.js). Every function that depends on
// the current time takes an explicit `now` (epoch ms) so tests are deterministic.
// Keep `function` / `var` declarations at top level: GJS legacy module exports are
// the module's properties, and `const` bindings are not exported.

var COLORS = {
    claude: '#D97757',
    codex: '#10A37F',
    other: '#9a9996',
    warn: '#F6D32D',
    crit: '#ED333B',
};

var DEFAULT_WARN = 80;
var DEFAULT_CRIT = 95;
var DEFAULT_REFRESH_S = 60;
var STALE_FACTOR = 3;
var DAY_MS = 24 * 60 * 60 * 1000;
var NBSP = '\u00a0';
var DAY_ABBR = ['dim.', 'lun.', 'mar.', 'mer.', 'jeu.', 'ven.', 'sam.'];
var EM_DASH = '\u2014';
var RESET_GLYPH = '\u27f2';

function pad2(n) {
    return (n < 10 ? '0' : '') + n;
}

function isNum(x) {
    return typeof x === 'number' && isFinite(x);
}

// 3.2 -> '3,2' (French decimal comma)
function frDecimal(x, decimals) {
    return x.toFixed(decimals).replace('.', ',');
}

function scaled(v, unit) {
    if (v >= 100)
        return `${Math.round(v)} ${unit}`;
    let s = frDecimal(v, 1);
    if (s.endsWith(',0'))
        s = s.slice(0, -2);
    return `${s} ${unit}`;
}

// Compact French counts: 850 -> '850', 850000 -> '850 k', 3200000 -> '3,2 M',
// 1200000000 -> '1,2 Md'. null/NaN -> em dash.
function formatCount(n) {
    if (!isNum(n))
        return EM_DASH;
    const sign = n < 0 ? '-' : '';
    const a = Math.abs(n);
    if (a < 1000)
        return sign + String(Math.round(a));
    if (a < 1e6) {
        if (Math.round(a / 1e3) >= 1000)
            return sign + scaled(a / 1e6, 'M');
        return sign + scaled(a / 1e3, 'k');
    }
    if (a < 1e9)
        return sign + scaled(a / 1e6, 'M');
    return sign + scaled(a / 1e9, 'Md');
}

// 62.4 -> '62 %' (thin no-break space before %, as in DESIGN §2)
function formatPercent(p) {
    if (!isNum(p))
        return EM_DASH;
    return `${Math.round(p)}${NBSP}%`;
}

// 62.4 -> '62%' (panel chip)
function formatPercentShort(p) {
    if (!isNum(p))
        return EM_DASH;
    return `${Math.round(p)}%`;
}

function clampPercent(p) {
    if (!isNum(p))
        return null;
    return Math.min(100, Math.max(0, p));
}

// ISO 8601 string -> epoch ms, or null when missing/invalid.
function parseIsoMs(s) {
    if (typeof s !== 'string' || s.length === 0)
        return null;
    const t = Date.parse(s);
    return isNaN(t) ? null : t;
}

// Local (machine TZ) HH:MM for an epoch ms value.
function formatHourMinute(ms) {
    const d = new Date(ms);
    return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

// 5400000 -> '1 h 30', 2h -> '2 h', 45 min -> '45 min', <1 min -> '< 1 min'
function formatCountdown(ms) {
    if (!isNum(ms) || ms <= 0)
        return 'maintenant';
    const totalMin = Math.floor(ms / 60000);
    if (totalMin < 1)
        return '< 1 min';
    const h = Math.floor(totalMin / 60);
    const m = totalMin % 60;
    if (h === 0)
        return `${m} min`;
    if (m === 0)
        return `${h} h`;
    return `${h} h ${pad2(m)}`;
}

// DESIGN §2: under 24 h -> '⟲ HH:MM · <countdown>', else '⟲ <jour abrégé> HH:MM'.
// Returns null when resetsAt is missing/invalid.
function formatResetText(resetsAt, now) {
    const t = parseIsoMs(resetsAt);
    if (t === null)
        return null;
    const hm = formatHourMinute(t);
    const diff = t - now;
    if (diff < DAY_MS)
        return `${RESET_GLYPH} ${hm} · ${formatCountdown(diff)}`;
    const d = new Date(t);
    return `${RESET_GLYPH} ${DAY_ABBR[d.getDay()]} ${hm}`;
}

// Text for a window's value: 'réinitialisé' after a reset, otherwise the percent.
function formatWindowValue(win) {
    if (!win)
        return EM_DASH;
    if (win.reset_since_observation)
        return 'réinitialisé';
    return formatPercent(win.used_percent);
}

// Level for a percent: 'crit' >= crit, 'warn' >= warn, else 'normal'.
function windowLevel(percent, warn, crit) {
    const w = isNum(warn) ? warn : DEFAULT_WARN;
    const c = isNum(crit) ? crit : DEFAULT_CRIT;
    if (!isNum(percent))
        return 'normal';
    if (percent >= c)
        return 'crit';
    if (percent >= w)
        return 'warn';
    return 'normal';
}

// Window with the highest used_percent (the "binding" quota), or null.
function bindingWindow(windows) {
    if (!Array.isArray(windows))
        return null;
    let best = null;
    for (const w of windows) {
        if (!w || !isNum(w.used_percent))
            continue;
        if (best === null || w.used_percent > best.used_percent)
            best = w;
    }
    return best;
}

// { stale: bool, ageS: number|null } from state.generated_at vs now.
// Stale when age > 3 x refresh_interval_s (DESIGN §1 / STATE_SCHEMA).
function stalenessOf(state, now) {
    const gen = parseIsoMs(state ? state.generated_at : null);
    if (gen === null)
        return { stale: true, ageS: null };
    const interval = state && isNum(state.refresh_interval_s) && state.refresh_interval_s > 0
        ? state.refresh_interval_s
        : DEFAULT_REFRESH_S;
    const ageS = Math.max(0, (now - gen) / 1000);
    return { stale: ageS > STALE_FACTOR * interval, ageS };
}

// A provider is shown dimmed when the whole state is stale or its plan data is stale.
function isProviderStale(provider, state, now) {
    const stateStale = stalenessOf(state, now).stale;
    const planStale = !!(provider && provider.plan && provider.plan.stale);
    return stateStale || planStale;
}

// Providers to render, in fixed order claude, codex. Skips unavailable ones
// and ones hidden in settings.
function visibleProviders(state, opts) {
    const o = opts || {};
    if (!state || !state.providers)
        return [];
    const out = [];
    const order = [['claude', o.showClaude !== false], ['codex', o.showCodex !== false]];
    for (const [id, shown] of order) {
        const p = state.providers[id];
        if (!shown || !p || p.available === false)
            continue;
        out.push(p);
    }
    return out;
}

function providerColor(id, level) {
    if (level === 'crit')
        return COLORS.crit;
    if (level === 'warn')
        return COLORS.warn;
    if (id === 'claude')
        return COLORS.claude;
    if (id === 'codex')
        return COLORS.codex;
    return COLORS.other;
}

// Label colour only when at warn/crit; null means "theme default".
function labelColor(level) {
    if (level === 'crit')
        return COLORS.crit;
    if (level === 'warn')
        return COLORS.warn;
    return null;
}

// Key used for notification dedupe. resets_at is rounded to the nearest 15 min
// (epoch ms / 900000) so small jitter in the API reset time does not re-notify.
var RESET_BUCKET_MS = 15 * 60 * 1000;
function notificationKey(providerId, win, level) {
    const t = parseIsoMs(win.resets_at);
    const bucket = t === null ? '' : String(Math.round(t / RESET_BUCKET_MS));
    return `${providerId}|${win.id}|${bucket}|${level}`;
}

// 'Claude Code — Session 5 h à 82 % (réinit. 16:00)'
function notificationText(providerLabel, win, level, resetsAt) {
    const label = win.label || win.id;
    const pct = formatPercent(win.used_percent);
    const t = parseIsoMs(resetsAt);
    const reset = t === null ? '' : ` (réinit. ${formatHourMinute(t)})`;
    return `${providerLabel} — ${label} à ${pct}${reset}`;
}

// 'il y a 40 s' / 'il y a 3 min' / 'il y a 2 h' / 'il y a 3 j'
function relativeAge(ageS) {
    if (!isNum(ageS))
        return 'date inconnue';
    const s = Math.max(0, Math.floor(ageS));
    if (s < 60)
        return `il y a ${s} s`;
    const m = Math.floor(s / 60);
    if (m < 60)
        return `il y a ${m} min`;
    const h = Math.floor(m / 60);
    if (h < 24)
        return `il y a ${h} h`;
    return `il y a ${Math.floor(h / 24)} j`;
}

// Short notice shown under a provider header for plan errors / stale values.
// Returns null when there is nothing to say.
function planNotice(plan) {
    if (!plan)
        return null;
    const parts = [];
    switch (plan.error) {
    case null:
    case undefined:
        break;
    case 'token_expired':
        parts.push('Token expiré — lance `claude` pour le rafraîchir');
        break;
    case 'http_429':
        parts.push("Limite d'appels API — nouvel essai plus tard");
        break;
    case 'no_credentials':
        parts.push('Claude Code non configuré sur ce poste');
        break;
    case 'logged_out':
        parts.push('Claude Code déconnecté — lance « claude » puis /login');
        break;
    default:
        parts.push(`Quota indisponible (${plan.error})`);
    }
    const observed = parseIsoMs(plan.observed_at);
    if (plan.stale && observed !== null)
        parts.push(`(valeurs du ${formatHourMinute(observed)})`);
    if (parts.length === 0)
        return null;
    return parts.join(' ');
}

// 'Aujourd'hui 3,2 M · 7 j 41,3 M · 30 j 163 M'
function formatTokensSummary(tokens) {
    const t = tokens || {};
    const tot = (k) => (t[k] && isNum(t[k].total) ? formatCount(t[k].total) : EM_DASH);
    return `Aujourd'hui ${tot('today')} · 7 j ${tot('last_7d')} · 30 j ${tot('last_30d')}`;
}

// Date-menu card footer: '3,2 M + 25,4 M tokens aujourd'hui'
function formatTodayTokensLine(providers) {
    const parts = [];
    for (const p of providers || []) {
        const today = p && p.tokens && p.tokens.today;
        if (today && isNum(today.total))
            parts.push(formatCount(today.total));
    }
    if (parts.length === 0)
        return `0 tokens aujourd'hui`;
    return `${parts.join(' + ')} tokens aujourd'hui`;
}

// Normalised heights (0..1) for the 14-day sparkline, oldest first.
function sparklineHeights(daily, n) {
    const count = n || 14;
    const src = Array.isArray(daily) ? daily : [];
    const vals = [];
    for (let i = Math.max(0, src.length - count); i < src.length; i++)
        vals.push(src[i] && isNum(src[i].total) ? Math.max(0, src[i].total) : 0);
    while (vals.length < count)
        vals.unshift(0);
    const max = Math.max(...vals);
    if (max <= 0)
        return vals.map(() => 0);
    return vals.map(v => v / max);
}

// 'opus-5-5 72 % · haiku-5-5 28 %' (top N models of the last 7 days)
function formatTopModels(byModel, n) {
    if (!Array.isArray(byModel))
        return '';
    return byModel
        .filter(m => m && typeof m.model === 'string' && isNum(m.share))
        .slice(0, n || 2)
        .map(m => `${m.model.replace(/^claude-/, '')} ${formatPercent(m.share * 100)}`)
        .join(' · ');
}

// Windows for the date-menu card: 5 h first, then weekly, then the rest; max N.
function selectCardWindows(windows, max) {
    if (!Array.isArray(windows))
        return [];
    const rank = (w) => (w.window_minutes === 300 ? 0 : (w.window_minutes === 10080 ? 1 : 2));
    return windows
        .filter(w => w && typeof w === 'object')
        .map((w, i) => ({ w, i }))
        .sort((a, b) => (rank(a.w) - rank(b.w)) || (a.i - b.i))
        .slice(0, max || 2)
        .map(x => x.w);
}

// Panel chip text. mode: 'percent' | 'tokens' | 'both'. Without any quota
// window the percent falls back to today's tokens.
function panelChipText(provider, mode) {
    const tokText = formatCount(provider && provider.tokens && provider.tokens.today
        ? provider.tokens.today.total : null);
    const bw = bindingWindow(provider ? provider.windows : null);
    const pctText = bw ? formatPercentShort(bw.used_percent) : null;
    if (mode === 'tokens')
        return tokText;
    if (mode === 'both')
        return pctText ? `${pctText} · ${tokText}` : tokText;
    return pctText || tokText;
}

// Display string for the label of a window (fallback on id).
function windowLabel(win) {
    return (win && (win.label || win.id)) || '';
}

// Hex '#RRGGBB' -> [r, g, b] in 0..1 for Cairo.
function hexToRgb(hex) {
    const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex || '');
    if (!m)
        return [0.5, 0.5, 0.5];
    return [parseInt(m[1], 16) / 255, parseInt(m[2], 16) / 255, parseInt(m[3], 16) / 255];
}

if (typeof module !== 'undefined') {
    module.exports = {
        COLORS, DEFAULT_WARN, DEFAULT_CRIT, DEFAULT_REFRESH_S, STALE_FACTOR,
        formatCount, formatPercent, formatPercentShort, clampPercent,
        parseIsoMs, formatHourMinute, formatCountdown, formatResetText,
        formatWindowValue, windowLevel, bindingWindow, stalenessOf,
        isProviderStale, visibleProviders, providerColor, labelColor,
        notificationKey, notificationText, relativeAge, planNotice,
        formatTokensSummary, formatTodayTokensLine, sparklineHeights,
        formatTopModels, selectCardWindows, panelChipText, windowLabel,
        hexToRgb,
    };
}
