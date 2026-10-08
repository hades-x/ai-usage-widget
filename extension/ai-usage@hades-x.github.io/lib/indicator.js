// -*- mode: js; js-indent-level: 4; indent-tabs-mode: nil -*-
/* exported ProviderIndicator, ROLE */
// Top-bar indicator (PanelMenu.Button) + popup (DESIGN §1–§2).
// Owns: a Gio.Cancellable, Gio.Subprocess objects, painters, one menu
// 'open-state-changed' handler. The 30 s timer lives in extension.js. destroy() releases all of them.

const { Gio, GObject, St, Clutter } = imports.gi;
const PanelMenu = imports.ui.panelMenu;
const PopupMenu = imports.ui.popupMenu;
const ExtensionUtils = imports.misc.extensionUtils;

const Me = ExtensionUtils.getCurrentExtension();
const Format = Me.imports.lib.format;
const Bars = Me.imports.lib.bars;

var ROLE = 'ai-usage';
const COLLECTOR_UNIT = 'ai-usage-collector.service';
const MUTED = '#9a9996';

function providerIconPath(extPath, id) {
    return `${extPath}/icons/${id === 'codex' ? 'codex' : 'claude'}-symbolic.svg`;
}

var ProviderIndicator = GObject.registerClass(
class ProviderIndicator extends PanelMenu.Button {
    // ctx: { settings: Gio.Settings, extensionPath: string, openPrefs: fn }
    _init(ctx) {
        super._init(0.0, 'AI Usage', false);
        this._ctx = ctx;
        this._state = null;
        this._status = 'missing';
        this._painters = [];
        this._procs = new Set();
        this._cancellable = new Gio.Cancellable();

        this._box = new St.BoxLayout({ style_class: 'ai-usage-indicator', y_align: Clutter.ActorAlign.CENTER });
        this.add_actor(this._box);
        this.add_style_class_name('ai-usage-panel-button');

        // GNOME 43 PopupMenu.open() returns early when the menu is empty, so the content is
        // built eagerly (here and in setState); on open it is rebuilt once for fresh countdowns.
        this._openId = this.menu.connect('open-state-changed', (_m, open) => {
            if (open)
                this._renderMenu();
        });

        this._render(true);
    }

    setState(result) {
        this._status = result ? result.status : 'missing';
        this._state = result && result.state ? result.state : null;
        this._render(true);
    }

    // 30 s timer (extension.js): chips always, popup only while it is open (countdowns).
    tick() {
        this._render(this.menu.isOpen);
    }

    _settingsValues() {
        const s = this._ctx.settings;
        return {
            mode: s.get_string('panel-mode'),
            warn: s.get_int('warn-threshold'),
            crit: s.get_int('crit-threshold'),
            showClaude: s.get_boolean('show-claude'),
            showCodex: s.get_boolean('show-codex'),
        };
    }

    // Destroys painters of one kind ('chip' | 'menu') and forgets them.
    _releasePainters(kind) {
        const keep = [];
        for (const p of this._painters) {
            if (p.kind === kind)
                p.destroy();
            else
                keep.push(p);
        }
        this._painters = keep;
    }

    _track(painter, kind) {
        painter.kind = kind;
        this._painters.push(painter);
        return painter;
    }

    _render(withMenu) {
        if (!this._ctx)
            return;
        this._renderChips();
        if (withMenu)
            this._renderMenu();
    }

    // ---------------------------------------------------------------- chips
    _renderChips() {
        // Release painters BEFORE their actors are destroyed by destroy_all_children().
        this._releasePainters('chip');
        this._box.destroy_all_children();

        const v = this._settingsValues();
        const providers = this._state
            ? Format.visibleProviders(this._state, { showClaude: v.showClaude, showCodex: v.showCodex })
            : [];

        if (this._status !== 'ok' || !this._state || providers.length === 0) {
            this._box.add_child(this._missingChip());
            return;
        }

        const now = Date.now();
        for (const p of providers) {
            const chip = this._chip(p, v, now);
            this._box.add_child(chip);
        }
    }

    _missingChip() {
        const box = new St.BoxLayout({ style_class: 'ai-usage-chip', y_align: Clutter.ActorAlign.CENTER });
        box.add_child(new St.Icon({
            gicon: Gio.icon_new_for_string(providerIconPath(this._ctx.extensionPath, 'claude')),
            icon_size: 16,
            y_align: Clutter.ActorAlign.CENTER,
            opacity: 110,
        }));
        box.add_child(new St.Label({
            text: '\u2014',
            y_align: Clutter.ActorAlign.CENTER,
            style: `color: ${MUTED};`,
        }));
        return box;
    }

    _chip(p, v, now) {
        const bw = Format.bindingWindow(p.windows);
        const level = bw ? Format.windowLevel(bw.used_percent, v.warn, v.crit) : 'normal';
        const color = Format.providerColor(p.id, level);
        const stale = Format.isProviderStale(p, this._state, now);

        const box = new St.BoxLayout({ style_class: 'ai-usage-chip', y_align: Clutter.ActorAlign.CENTER });
        box.add_child(new St.Icon({
            gicon: Gio.icon_new_for_string(providerIconPath(this._ctx.extensionPath, p.id)),
            icon_size: 16,
            y_align: Clutter.ActorAlign.CENTER,
        }));

        const gauge = this._track(new Bars.VerticalGauge(4, 14), 'chip');
        gauge.setValue(bw ? Format.clampPercent(bw.used_percent) : null, color);
        box.add_child(gauge.actor);

        const lc = Format.labelColor(level);
        let style = lc ? `color: ${lc};` : '';
        if (level === 'crit')
            style += ' font-weight: bold;';
        box.add_child(new St.Label({
            text: Format.panelChipText(p, v.mode),
            y_align: Clutter.ActorAlign.CENTER,
            style,
        }));

        if (stale)
            box.opacity = 128;
        return box;
    }

    // ---------------------------------------------------------------- popup
    _renderMenu() {
        this._releasePainters('menu');
        this.menu.removeAll();
        const v = this._settingsValues();
        const now = Date.now();

        if (this._status === 'missing') {
            this._addInfo('Collecteur non démarré — lance « Actualiser ».');
        } else if (this._status === 'invalid') {
            this._addInfo('État illisible (state.json invalide).');
        } else {
            const providers = Format.visibleProviders(this._state, { showClaude: v.showClaude, showCodex: v.showCodex });
            if (providers.length === 0)
                this._addInfo('Aucun fournisseur affiché.');
            providers.forEach((p, i) => {
                if (i > 0)
                    this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
                this._addProvider(p, v, now);
            });
        }
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._addFooter(now);
    }

    _item() {
        return new PopupMenu.PopupBaseMenuItem({ reactive: false, can_focus: false, activate: false });
    }

    _addInfo(text) {
        const item = this._item();
        item.add_child(new St.Label({ text, style_class: 'ai-usage-dim', x_expand: true }));
        this.menu.addMenuItem(item);
    }

    _addProvider(p, v, now) {
        const stale = Format.isProviderStale(p, this._state, now);
        const col = new St.BoxLayout({ vertical: true, x_expand: true, style_class: 'ai-usage-section' });
        if (stale)
            col.opacity = 170;

        // header: icon, label, plan badge
        const head = new St.BoxLayout({ style_class: 'ai-usage-header', x_expand: true });
        head.add_child(new St.Icon({
            gicon: Gio.icon_new_for_string(providerIconPath(this._ctx.extensionPath, p.id)),
            icon_size: 16,
            y_align: Clutter.ActorAlign.CENTER,
        }));
        head.add_child(new St.Label({
            text: p.label || p.id,
            style_class: 'ai-usage-title',
            x_expand: true,
            y_align: Clutter.ActorAlign.CENTER,
        }));
        if (p.plan && p.plan.label) {
            head.add_child(new St.Label({
                text: p.plan.label,
                style_class: 'ai-usage-badge',
                y_align: Clutter.ActorAlign.CENTER,
            }));
        }
        col.add_child(head);

        const notice = Format.planNotice(p.plan);
        if (notice)
            col.add_child(new St.Label({ text: notice, style_class: 'ai-usage-dim ai-usage-notice' }));

        for (const win of (p.windows || [])) {
            if (win)
                col.add_child(this._windowRow(p, win, v, now));
        }

        col.add_child(new St.Label({
            text: Format.formatTokensSummary(p.tokens),
            style_class: 'ai-usage-dim',
        }));

        const spark = this._track(new Bars.Sparkline(84, 14), 'menu');
        spark.setValue(Format.sparklineHeights(p.tokens ? p.tokens.daily_14d : null), Format.providerColor(p.id, 'normal'));
        const sparkRow = new St.BoxLayout({ style_class: 'ai-usage-spark-row' });
        sparkRow.add_child(spark.actor);
        sparkRow.add_child(new St.Label({
            text: `(14 j)  ${Format.formatTopModels(p.tokens ? p.tokens.by_model_7d : null, 2)}`,
            style_class: 'ai-usage-dim',
            y_align: Clutter.ActorAlign.CENTER,
        }));
        col.add_child(sparkRow);

        const item = this._item();
        item.add_child(col);
        this.menu.addMenuItem(item);
    }

    _windowRow(p, win, v, now) {
        const level = Format.windowLevel(win.used_percent, v.warn, v.crit);
        const color = Format.providerColor(p.id, level);
        const row = new St.BoxLayout({ style_class: 'ai-usage-window-row', x_expand: true });

        row.add_child(new St.Label({
            text: Format.windowLabel(win),
            style_class: 'ai-usage-win-label',
            y_align: Clutter.ActorAlign.CENTER,
        }));

        const bar = this._track(new Bars.QuotaBar(140, 8), 'menu');
        bar.setValue({
            percent: Format.clampPercent(win.used_percent),
            elapsed: Format.clampPercent(win.elapsed_percent),
            color,
            empty: !!win.reset_since_observation,
        });
        row.add_child(bar.actor);

        const lc = Format.labelColor(level);
        row.add_child(new St.Label({
            text: Format.formatWindowValue(win),
            style_class: 'ai-usage-value',
            y_align: Clutter.ActorAlign.CENTER,
            style: lc ? `color: ${lc};` : '',
        }));

        const reset = win.reset_since_observation ? null : Format.formatResetText(win.resets_at, now);
        row.add_child(new St.Label({
            text: reset || '',
            style_class: 'ai-usage-reset',
            y_align: Clutter.ActorAlign.CENTER,
        }));
        return row;
    }

    _addFooter(now) {
        const item = this._item();
        const row = new St.BoxLayout({ style_class: 'ai-usage-footer', x_expand: true });
        const ageS = this._state ? Format.stalenessOf(this._state, now).ageS : null;
        const ageText = this._status === 'ok'
            ? `Mis à jour ${Format.relativeAge(ageS)}`
            : 'Pas de données';
        row.add_child(new St.Label({
            text: ageText,
            style_class: 'ai-usage-dim',
            x_expand: true,
            y_align: Clutter.ActorAlign.CENTER,
        }));

        const refresh = new St.Button({
            label: '⟳ Actualiser',
            style_class: 'ai-usage-button',
            can_focus: true,
        });
        refresh.connect('clicked', () => this._onRefresh());
        row.add_child(refresh);

        const gear = new St.Button({
            label: '⚙',
            style_class: 'ai-usage-button',
            can_focus: true,
            accessible_name: 'Préférences',
        });
        gear.connect('clicked', () => {
            this.menu.close();
            this._ctx.openPrefs();
        });
        row.add_child(gear);

        item.add_child(row);
        this.menu.addMenuItem(item);
    }

    // Runs the collector once, asynchronously. Only this fixed argv is ever executed.
    _onRefresh() {
        if (!this._ctx)
            return;
        try {
            const proc = Gio.Subprocess.new(
                ['systemctl', '--user', 'start', COLLECTOR_UNIT],
                Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE);
            this._procs.add(proc);
            proc.wait_async(this._cancellable, (p, res) => {
                try {
                    p.wait_finish(res);
                } catch (e) {
                    // cancelled on destroy, or the unit failed: state.json reload covers both
                }
                this._procs.delete(p);
            });
        } catch (e) {
            log('ai-usage: could not start collector unit');
        }
    }

    destroy() {
        if (this._openId) {
            this.menu.disconnect(this._openId);
            this._openId = 0;
        }
        if (this._cancellable) {
            this._cancellable.cancel();
            this._cancellable = null;
        }
        this._procs.clear();
        this._releasePainters('chip');
        this._releasePainters('menu');
        this._ctx = null;
        this._state = null;
        super.destroy();
    }
});
