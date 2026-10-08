// -*- js-indent-level: 4 -*-
// Plain node test runner for the pure JS in extension/ai-usage@hades-x.github.io/lib/format.js.
// No npm deps. Exits non-zero on any failure.
'use strict';

// Fixed timezone so local-time expectations are deterministic (DESIGN §2: fr_FR, Paris).
// Must stay before any Date use and before any require that may compute local times:
// Node re-reads TZ on assignment, so this is effective for every later call.
process.env.TZ = 'Europe/Paris';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..');
const fmt = require(path.join(ROOT, 'ai-usage@hades-x.github.io', 'lib', 'format.js'));
// Synthetic fixture: every value-specific assertion uses it, never the docs sample.
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'state.json'), 'utf8'));

const NBSP = '\u00a0';
const RESET = '\u27f2';
const NOW = Date.parse(fixture.generated_at); // injected clock for every time-dependent call

let passed = 0;
const failures = [];

function test(name, fn) {
    try {
        fn();
        passed++;
        console.log(`  ok   ${name}`);
    } catch (e) {
        failures.push({ name, e });
        console.log(`  FAIL ${name}`);
        console.log(`       ${String(e.message).split('\n').join('\n       ')}`);
    }
}

function clone(o) {
    return JSON.parse(JSON.stringify(o));
}

// ---------------------------------------------------------------- formatCount
test('formatCount: small integers and French scales', () => {
    assert.equal(fmt.formatCount(0), '0');
    assert.equal(fmt.formatCount(850), '850');
    assert.equal(fmt.formatCount(850000), '850 k');
    assert.equal(fmt.formatCount(3232200), '3,2 M');
    assert.equal(fmt.formatCount(3000000), '3 M');
    assert.equal(fmt.formatCount(41292000), '41,3 M');
    assert.equal(fmt.formatCount(163100000), '163 M');
    assert.equal(fmt.formatCount(1200000000), '1,2 Md');
    assert.equal(fmt.formatCount(423100000), '423 M');
});

test('formatCount: boundaries roll over to the next unit', () => {
    assert.equal(fmt.formatCount(999), '999');
    assert.equal(fmt.formatCount(999960), '1 M');
    assert.equal(fmt.formatCount(999999999), '1000 M');
});

test('formatCount: null, undefined, NaN -> em dash; negatives keep sign', () => {
    assert.equal(fmt.formatCount(null), '\u2014');
    assert.equal(fmt.formatCount(undefined), '\u2014');
    assert.equal(fmt.formatCount(NaN), '\u2014');
    assert.equal(fmt.formatCount(-2500), '-2,5 k');
});

// ------------------------------------------------------------- percent helpers
test('formatPercent uses a no-break space before %, rounds half up', () => {
    assert.equal(fmt.formatPercent(62.4), `62${NBSP}%`);
    assert.equal(fmt.formatPercent(62.5), `63${NBSP}%`);
    assert.equal(fmt.formatPercent(100), `100${NBSP}%`);
    assert.equal(fmt.formatPercent(null), '\u2014');
});

test('formatPercentShort (chip) has a plain space-less suffix', () => {
    assert.equal(fmt.formatPercentShort(61.6), '62%');
    assert.equal(fmt.formatPercentShort(undefined), '\u2014');
});

test('clampPercent clamps to 0..100 and maps non-numbers to null', () => {
    assert.equal(fmt.clampPercent(-3), 0);
    assert.equal(fmt.clampPercent(120), 100);
    assert.equal(fmt.clampPercent(42.5), 42.5);
    assert.equal(fmt.clampPercent('x'), null);
});

// -------------------------------------------------------------- time helpers
test('parseIsoMs parses Z timestamps and rejects junk', () => {
    assert.equal(fmt.parseIsoMs('2026-10-08T14:30:00Z'), NOW);
    assert.equal(fmt.parseIsoMs('not a date'), null);
    assert.equal(fmt.parseIsoMs(''), null);
    assert.equal(fmt.parseIsoMs(null), null);
    assert.equal(fmt.parseIsoMs(42), null);
});

test('formatHourMinute renders local Paris time', () => {
    // 16:00 UTC in October (CEST, UTC+2) -> 18:00 local
    assert.equal(fmt.formatHourMinute(Date.parse('2026-10-08T16:00:00Z')), '18:00');
    assert.equal(fmt.formatHourMinute(Date.parse('2026-10-08T23:05:00Z')), '01:05');
});

test('formatCountdown: hours and minutes', () => {
    assert.equal(fmt.formatCountdown(90 * 60000), '1 h 30');
    assert.equal(fmt.formatCountdown(2 * 3600000), '2 h');
    assert.equal(fmt.formatCountdown(45 * 60000), '45 min');
    assert.equal(fmt.formatCountdown(30000), '< 1 min');
    assert.equal(fmt.formatCountdown(0), 'maintenant');
    assert.equal(fmt.formatCountdown(-5000), 'maintenant');
    assert.equal(fmt.formatCountdown(3 * 3600000 + 5 * 60000), '3 h 05');
});

test('formatResetText: under 24 h shows HH:MM + countdown (5 h window)', () => {
    // resets 16:00Z = 18:00 Paris, now 14:30Z -> 1h30 left
    const t = fmt.formatResetText('2026-10-08T16:00:00Z', NOW);
    assert.equal(t, `${RESET} 18:00 · 1 h 30`);
});

test('formatResetText: 24 h or more shows abbreviated weekday + HH:MM', () => {
    // 2026-10-11T23:00Z = Monday 01:00 Paris (DST: UTC+2)
    assert.equal(fmt.formatResetText('2026-10-11T23:00:00Z', NOW), `${RESET} lun. 01:00`);
    // 2026-10-14T16:58:24Z = Wednesday 18:58 Paris
    assert.equal(fmt.formatResetText('2026-10-14T16:58:24Z', NOW), `${RESET} mer. 18:58`);
});

test('formatResetText: already past and invalid inputs', () => {
    assert.equal(fmt.formatResetText('2026-10-08T14:00:00Z', NOW), `${RESET} 16:00 · maintenant`);
    assert.equal(fmt.formatResetText(null, NOW), null);
    assert.equal(fmt.formatResetText('garbage', NOW), null);
});

test('stalenessOf: fresh, stale (> 3 x interval), missing generated_at', () => {
    const fresh = fmt.stalenessOf(fixture, NOW + 60 * 1000);
    assert.equal(fresh.stale, false);
    assert.equal(fresh.ageS, 60);
    const stale = fmt.stalenessOf(fixture, NOW + 181 * 1000);
    assert.equal(stale.stale, true);
    const exact = fmt.stalenessOf(fixture, NOW + 180 * 1000);
    assert.equal(exact.stale, false, 'exactly 3 x interval is not stale');
    const missing = fmt.stalenessOf({ generated_at: null }, NOW);
    assert.deepEqual(missing, { stale: true, ageS: null });
});

test('stalenessOf: missing refresh_interval_s falls back to 60 s', () => {
    const s = clone(fixture);
    delete s.refresh_interval_s;
    assert.equal(fmt.stalenessOf(s, NOW + 170 * 1000).stale, false);
    assert.equal(fmt.stalenessOf(s, NOW + 181 * 1000).stale, true);
});

test('stalenessOf: future generated_at clamps age to 0 (clock skew)', () => {
    assert.deepEqual(fmt.stalenessOf(fixture, NOW - 5000), { stale: false, ageS: 0 });
});

test('relativeAge: seconds, minutes, hours, days, unknown', () => {
    assert.equal(fmt.relativeAge(40), 'il y a 40 s');
    assert.equal(fmt.relativeAge(0.4), 'il y a 0 s');
    assert.equal(fmt.relativeAge(180), 'il y a 3 min');
    assert.equal(fmt.relativeAge(3 * 3600 + 10), 'il y a 3 h');
    assert.equal(fmt.relativeAge(3 * 86400), 'il y a 3 j');
    assert.equal(fmt.relativeAge(null), 'date inconnue');
});

// -------------------------------------------------------- windows & providers
test('bindingWindow: max used_percent, ignores null windows and nulls', () => {
    const w = fmt.bindingWindow(fixture.providers.claude.windows);
    assert.equal(w.id, 'five_hour');
    assert.equal(fmt.bindingWindow([null, { id: 'a', used_percent: null }, { id: 'b', used_percent: 12 }, { id: 'c', used_percent: 40 }]).id, 'c');
    assert.equal(fmt.bindingWindow([]), null);
    assert.equal(fmt.bindingWindow(null), null);
    assert.equal(fmt.bindingWindow(undefined), null);
});

test('bindingWindow: used_percent above 100 is kept (binding)', () => {
    const w = fmt.bindingWindow([{ id: 'x', used_percent: 104.2 }, { id: 'y', used_percent: 99 }]);
    assert.equal(w.id, 'x');
    assert.equal(fmt.formatPercentShort(w.used_percent), '104%');
});

test('windowLevel: normal / warn / crit with defaults and custom thresholds', () => {
    assert.equal(fmt.windowLevel(79.9, 80, 95), 'normal');
    assert.equal(fmt.windowLevel(80, 80, 95), 'warn');
    assert.equal(fmt.windowLevel(94.9, 80, 95), 'warn');
    assert.equal(fmt.windowLevel(95, 80, 95), 'crit');
    assert.equal(fmt.windowLevel(104, undefined, undefined), 'crit');
    assert.equal(fmt.windowLevel(85, 90, 99), 'normal');
    assert.equal(fmt.windowLevel(null, 80, 95), 'normal');
});

test('formatWindowValue: reset_since_observation -> réinitialisé, else percent', () => {
    assert.equal(fmt.formatWindowValue({ used_percent: 0, reset_since_observation: true }), 'réinitialisé');
    assert.equal(fmt.formatWindowValue({ used_percent: 62, reset_since_observation: false }), `62${NBSP}%`);
    assert.equal(fmt.formatWindowValue(null), '\u2014');
});

test('visibleProviders: order claude, codex; hides unavailable and unchecked', () => {
    const ids = (arr) => arr.map(p => p.id);
    assert.deepEqual(ids(fmt.visibleProviders(fixture, {})), ['claude', 'codex']);
    assert.deepEqual(ids(fmt.visibleProviders(fixture, { showCodex: false })), ['claude']);
    assert.deepEqual(ids(fmt.visibleProviders(fixture, { showClaude: false, showCodex: false })), []);
    const s = clone(fixture);
    s.providers.codex.available = false;
    assert.deepEqual(ids(fmt.visibleProviders(s, {})), ['claude']);
    delete s.providers.claude;
    assert.deepEqual(ids(fmt.visibleProviders(s, {})), []);
    assert.deepEqual(fmt.visibleProviders(null, {}), []);
    assert.deepEqual(fmt.visibleProviders({}, {}), []);
});

test('isProviderStale: state stale OR plan.stale', () => {
    const claude = fixture.providers.claude;
    assert.equal(fmt.isProviderStale(claude, fixture, NOW), false);
    assert.equal(fmt.isProviderStale(claude, fixture, NOW + 200000), true);
    const planStale = clone(claude);
    planStale.plan.stale = true;
    assert.equal(fmt.isProviderStale(planStale, fixture, NOW), true);
});

test('providerColor and labelColor follow DESIGN §1', () => {
    assert.equal(fmt.providerColor('claude', 'normal'), '#D97757');
    assert.equal(fmt.providerColor('codex', 'normal'), '#10A37F');
    assert.equal(fmt.providerColor('claude', 'warn'), '#F6D32D');
    assert.equal(fmt.providerColor('codex', 'crit'), '#ED333B');
    assert.equal(fmt.labelColor('normal'), null);
    assert.equal(fmt.labelColor('warn'), '#F6D32D');
    assert.equal(fmt.labelColor('crit'), '#ED333B');
});

test('panelChipText: percent / tokens / both, with token fallback without windows', () => {
    const claude = fixture.providers.claude;
    assert.equal(fmt.panelChipText(claude, 'percent'), '62%');
    assert.equal(fmt.panelChipText(claude, 'tokens'), '3,2 M');
    assert.equal(fmt.panelChipText(claude, 'both'), '62% · 3,2 M');
    const noWin = clone(claude);
    noWin.windows = [];
    assert.equal(fmt.panelChipText(noWin, 'percent'), '3,2 M');
    assert.equal(fmt.panelChipText(noWin, 'both'), '3,2 M');
});

test('selectCardWindows: 5 h first, then weekly, max 2 by default', () => {
    const got = fmt.selectCardWindows(fixture.providers.claude.windows).map(w => w.id);
    assert.deepEqual(got, ['five_hour', 'seven_day']);
    const reordered = fmt.selectCardWindows([
        { id: 'seven_day', window_minutes: 10080 },
        { id: 'other', window_minutes: 60 },
        { id: 'five_hour', window_minutes: 300 },
    ], 3).map(w => w.id);
    assert.deepEqual(reordered, ['five_hour', 'seven_day', 'other']);
    assert.deepEqual(fmt.selectCardWindows(null), []);
});

test('sparklineHeights: 14 values, oldest first, normalised to max', () => {
    const h = fmt.sparklineHeights(fixture.providers.claude.tokens.daily_14d);
    assert.equal(h.length, 14);
    assert.equal(Math.max(...h), 1);
    assert.equal(h[0], 0);
    assert.equal(h[13], 3232200 / 12000000);
});

test('sparklineHeights: all zeros, short input padded, long input truncated', () => {
    assert.deepEqual(fmt.sparklineHeights([{ total: 0 }, { total: 0 }]), new Array(14).fill(0));
    const short = fmt.sparklineHeights([{ total: 10 }]);
    assert.equal(short.length, 14);
    assert.equal(short[13], 1);
    const long = Array.from({ length: 20 }, (_, i) => ({ total: i + 1 }));
    const h = fmt.sparklineHeights(long);
    assert.equal(h.length, 14);
    assert.equal(h[13], 1);
    assert.equal(h[0], 7 / 20);
    assert.equal(fmt.sparklineHeights(undefined).length, 14);
});

test('formatTopModels: strips claude- prefix, percent shares, top 2', () => {
    assert.equal(fmt.formatTopModels(fixture.providers.claude.tokens.by_model_7d, 2),
        `opus-5-5 72${NBSP}% · haiku-5-5 28${NBSP}%`);
    assert.equal(fmt.formatTopModels(fixture.providers.codex.tokens.by_model_7d, 2),
        `gpt-6.1-sol 100${NBSP}%`);
    assert.equal(fmt.formatTopModels([{ model: 'x', share: null }], 2), '');
    assert.equal(fmt.formatTopModels(undefined, 2), '');
});

test('formatTokensSummary matches DESIGN §2 line', () => {
    assert.equal(fmt.formatTokensSummary(fixture.providers.claude.tokens),
        "Aujourd'hui 3,2 M · 7 j 41,3 M · 30 j 163 M");
    assert.equal(fmt.formatTokensSummary(fixture.providers.codex.tokens),
        "Aujourd'hui 25,4 M · 7 j 170 M · 30 j 423 M");
    assert.equal(fmt.formatTokensSummary(null),
        "Aujourd'hui \u2014 · 7 j \u2014 · 30 j \u2014");
});

test('formatTodayTokensLine (date-menu card footer)', () => {
    assert.equal(fmt.formatTodayTokensLine([fixture.providers.claude, fixture.providers.codex]),
        "3,2 M + 25,4 M tokens aujourd'hui");
    assert.equal(fmt.formatTodayTokensLine([fixture.providers.codex]), "25,4 M tokens aujourd'hui");
    assert.equal(fmt.formatTodayTokensLine([]), "0 tokens aujourd'hui");
    assert.equal(fmt.formatTodayTokensLine([{ tokens: null }]), "0 tokens aujourd'hui");
});

test('planNotice: error mapping, stale suffix, nothing to say', () => {
    assert.equal(fmt.planNotice(fixture.providers.claude.plan), null);
    assert.equal(fmt.planNotice({ error: 'token_expired' }),
        'Token expiré — lance `claude` pour le rafraîchir');
    assert.equal(fmt.planNotice({ error: 'http_429', observed_at: null }),
        "Limite d'appels API — nouvel essai plus tard");
    assert.equal(fmt.planNotice({ error: 'no_credentials' }), 'Claude Code non configuré sur ce poste');
    assert.equal(fmt.planNotice({ error: 'logged_out' }),
        'Claude Code déconnecté — lance « claude » puis /login');
    assert.equal(fmt.planNotice({ error: 'logged_out', stale: true, observed_at: '2026-10-08T14:00:00Z' }),
        'Claude Code déconnecté — lance « claude » puis /login (valeurs du 16:00)');
    assert.equal(fmt.planNotice({ error: 'some_future_code' }), 'Quota indisponible (some_future_code)');
    assert.equal(fmt.planNotice({ error: 'http_5xx' }), 'Quota indisponible (http_5xx)');
    assert.equal(fmt.planNotice({ error: 'network', stale: true, observed_at: '2026-10-08T14:00:00Z' }),
        'Quota indisponible (network) (valeurs du 16:00)');
    assert.equal(fmt.planNotice({ error: null, stale: true, observed_at: '2026-10-08T14:00:00Z' }),
        '(valeurs du 16:00)');
    assert.equal(fmt.planNotice(null), null);
});

test('notificationKey: stable per (provider, window, reset bucket, level)', () => {
    const w = fixture.providers.claude.windows[0];
    // 16:00Z = 960 x 15 min buckets since epoch: 2026-10-08T16:00:00Z / 900000 is an integer
    const bucket = String(Date.parse('2026-10-08T16:00:00Z') / 900000);
    assert.equal(fmt.notificationKey('claude', w, 'warn'), `claude|five_hour|${bucket}|warn`);
    assert.notEqual(fmt.notificationKey('claude', w, 'warn'), fmt.notificationKey('claude', w, 'crit'));
    assert.equal(fmt.notificationKey('codex', { id: 'primary' }, 'warn'), 'codex|primary||warn');
});

test('notificationKey: API jitter of a few minutes keeps the same key', () => {
    const base = { id: 'five_hour', resets_at: '2026-10-08T16:00:00Z' };
    const k = fmt.notificationKey('claude', base, 'warn');
    for (const t of ['2026-10-08T16:00:24Z', '2026-10-08T15:53:00Z', '2026-10-08T16:07:00Z']) {
        assert.equal(fmt.notificationKey('claude', { id: 'five_hour', resets_at: t }, 'warn'), k, t);
    }
});

test('notificationKey: a reset more than half a bucket away is a new key', () => {
    const at = (t) => fmt.notificationKey('claude', { id: 'five_hour', resets_at: t }, 'warn');
    assert.notEqual(at('2026-10-08T16:08:00Z'), at('2026-10-08T16:00:00Z'));
    assert.notEqual(at('2026-10-08T21:00:00Z'), at('2026-10-08T16:00:00Z'));
});

test('notificationKey: unparseable resets_at gives an empty bucket', () => {
    assert.equal(fmt.notificationKey('codex', { id: 'primary', resets_at: 'junk' }, 'crit'), 'codex|primary||crit');
});

test('notificationText matches DESIGN §4 example', () => {
    const w = fixture.providers.claude.windows[0];
    assert.equal(
        fmt.notificationText('Claude Code', { id: 'five_hour', label: 'Session 5 h', used_percent: 82 }, 'warn', w.resets_at),
        `Claude Code — Session 5 h à 82${NBSP}% (réinit. 18:00)`);
    assert.equal(
        fmt.notificationText('Codex', { id: 'primary', used_percent: 96.2 }, 'crit', null),
        `Codex — primary à 96${NBSP}%`);
});

test('windowLabel falls back to id, handles null', () => {
    assert.equal(fmt.windowLabel({ id: 'primary', label: 'Semaine' }), 'Semaine');
    assert.equal(fmt.windowLabel({ id: 'primary' }), 'primary');
    assert.equal(fmt.windowLabel(null), '');
});

test('hexToRgb: parses #RRGGBB, falls back to grey on junk', () => {
    const [r, g, b] = fmt.hexToRgb('#D97757');
    assert.ok(Math.abs(r - 0xD9 / 255) < 1e-9);
    assert.ok(Math.abs(g - 0x77 / 255) < 1e-9);
    assert.ok(Math.abs(b - 0x57 / 255) < 1e-9);
    assert.deepEqual(fmt.hexToRgb('nope'), [0.5, 0.5, 0.5]);
});

// ----------------------------------------------------- end-to-end sanity on fixture
test('fixture state: chip + card texts', () => {
    const c = fixture.providers.claude;
    const bw = fmt.bindingWindow(c.windows);
    assert.equal(fmt.windowLevel(bw.used_percent, 80, 95), 'normal');
    assert.equal(fmt.formatResetText(bw.resets_at, NOW), `${RESET} 18:00 · 1 h 30`);
    assert.equal(fmt.formatResetText(c.windows[1].resets_at, NOW), `${RESET} lun. 01:00`);
});

// ------------------------------------------------- docs sample: shape smoke test only
// Deliberately asserts no concrete values from docs/examples/state.example.json, so the
// documentation can evolve without breaking CI. Value contracts live in the Python tests.
test('docs example renders well-formed outputs (shape only)', () => {
    const docs = JSON.parse(fs.readFileSync(path.join(ROOT, '..', 'docs', 'examples', 'state.example.json'), 'utf8'));
    const pct = /^\d+%$/;
    const providers = fmt.visibleProviders(docs, {});
    assert.ok(providers.length > 0, 'at least one provider is visible');
    for (const p of providers) {
        const chip = fmt.panelChipText(p, 'both');
        assert.equal(typeof chip, 'string');
        assert.ok(chip.length > 0, `${p.id}: chip text is non-empty`);
        const bw = fmt.bindingWindow(p.windows);
        if (bw) assert.match(fmt.panelChipText(p, 'percent'), pct, `${p.id}: chip percent parses`);
        assert.ok(fmt.formatTokensSummary(p.tokens).length > 0, `${p.id}: token summary non-empty`);
        const h = fmt.sparklineHeights(p.tokens.daily_14d);
        assert.equal(h.length, 14, `${p.id}: 14 sparkline values`);
        assert.ok(h.every(v => Number.isFinite(v) && v >= 0 && v <= 1), `${p.id}: sparkline in 0..1`);
        assert.ok(fmt.formatTodayTokensLine([p]).endsWith("tokens aujourd'hui"), `${p.id}: today line`);
    }
    assert.ok(fmt.formatTodayTokensLine(providers).length > 0);
});

// ------------------------------------------------------------------- summary
console.log('');
console.log(`${passed} passed, ${failures.length} failed, ${passed + failures.length} total`);
if (failures.length > 0) {
    for (const f of failures)
        console.log(`FAILED: ${f.name}`);
    process.exit(1);
}
process.exit(0);
