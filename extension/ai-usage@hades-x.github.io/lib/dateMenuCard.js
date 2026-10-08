// -*- mode: js; js-indent-level: 4; indent-tabs-mode: nil -*-
/* exported DateMenuCard */
// Card in the right column of the GNOME date menu (DESIGN §3).
//
// Insertion point (GNOME 43.9 js/ui/dateMenu.js): DateMenuButton keeps the right
// column scroll view in `_displaysSection`, whose single child is the vertical
// `displaysBox` that holds `_eventsItem`, `_clocksItem`, `_weatherItem`. The card is
// appended to that box, i.e. below the weather section. It is removed and destroyed
// in disable().

const { Gio, GObject, St, Clutter } = imports.gi;
const Main = imports.ui.main;
const ExtensionUtils = imports.misc.extensionUtils;

const Me = ExtensionUtils.getCurrentExtension();
const Format = Me.imports.lib.format;
const Bars = Me.imports.lib.bars;

var DateMenuCard = GObject.registerClass(
class DateMenuCard extends St.Button {
    // ctx: { extensionPath, onClick: fn }
    _init(ctx) {
        super._init({
            style_class: 'weather-button ai-usage-card',
            can_focus: true,
            x_expand: true,
        });
        this.accessible_name = 'Consommation IA';
        this._ctx = ctx;
        this._painters = [];

        this._box = new St.BoxLayout({ vertical: true, x_expand: true, style_class: 'weather-box' });
        this.child = this._box;

        const title = new St.BoxLayout({ style_class: 'weather-header-box' });
        title.add_child(new St.Label({
            text: 'Consommation IA',
            style_class: 'weather-header',
            x_align: Clutter.ActorAlign.START,
            x_expand: true,
        }));
        this._box.add_child(title);

        this._rows = new St.BoxLayout({ vertical: true, x_expand: true, style_class: 'ai-usage-card-rows' });
        this._box.add_child(this._rows);

        this._footer = new St.Label({ style_class: 'weather-header', text: '' });
        this._box.add_child(this._footer);
    }

    _releasePainters() {
        for (const p of this._painters)
            p.destroy();
        this._painters = [];
    }

    // state: parsed state.json or null. settings: Gio.Settings. now: epoch ms.
    render(state, settings, now) {
        this._releasePainters();
        this._rows.destroy_all_children();
        const warn = settings.get_int('warn-threshold');
        const crit = settings.get_int('crit-threshold');
        const providers = state
            ? Format.visibleProviders(state, {
                showClaude: settings.get_boolean('show-claude'),
                showCodex: settings.get_boolean('show-codex'),
            })
            : [];

        if (providers.length === 0) {
            this._rows.add_child(new St.Label({
                text: 'Aucune donnée — lance « Actualiser » dans le widget.',
                style_class: 'weather-forecast-time',
            }));
            this._footer.text = '';
            return;
        }

        for (const p of providers) {
            const stale = Format.isProviderStale(p, state, now);
            for (const win of Format.selectCardWindows(p.windows, 2)) {
                this._rows.add_child(this._row(p, win, warn, crit, stale));
            }
        }
        this._footer.text = Format.formatTodayTokensLine(providers);
    }

    _row(p, win, warn, crit, stale) {
        const level = Format.windowLevel(win.used_percent, warn, crit);
        const row = new St.BoxLayout({ style_class: 'ai-usage-card-row', x_expand: true });
        if (stale)
            row.opacity = 128;

        row.add_child(new St.Icon({
            gicon: Gio.icon_new_for_string(`${this._ctx.extensionPath}/icons/${p.id === 'codex' ? 'codex' : 'claude'}-symbolic.svg`),
            icon_size: 16,
            y_align: Clutter.ActorAlign.CENTER,
        }));
        row.add_child(new St.Label({
            text: Format.windowLabel(win),
            style_class: 'weather-forecast-time',
            y_align: Clutter.ActorAlign.CENTER,
            x_expand: true,
        }));

        const bar = new Bars.QuotaBar(90, 8);
        this._painters.push(bar);
        bar.setValue({
            percent: Format.clampPercent(win.used_percent),
            elapsed: null,
            color: Format.providerColor(p.id, level),
            empty: !!win.reset_since_observation,
        });
        row.add_child(bar.actor);

        const lc = Format.labelColor(level);
        row.add_child(new St.Label({
            text: Format.formatWindowValue(win),
            style_class: 'weather-forecast-temp',
            y_align: Clutter.ActorAlign.CENTER,
            style: lc ? `color: ${lc};` : '',
        }));
        return row;
    }

    vfunc_clicked() {
        this._ctx.onClick();
    }

    destroy() {
        this._releasePainters();
        this._ctx = null;
        super.destroy();
    }
});

// Right-column box of the date menu, or null if the shell layout differs.
function displaysBoxOf(dateMenu) {
    if (!dateMenu || !dateMenu._displaysSection)
        return null;
    const box = dateMenu._displaysSection.get_child();
    return box || null;
}

// Adds `card` to the date menu right column. Returns true on success.
function insertCard(dateMenu, card) {
    const box = displaysBoxOf(dateMenu);
    if (!box) {
        log('ai-usage: date menu right column not found; card not inserted');
        return false;
    }
    box.add_child(card);
    return true;
}

// Removes and destroys the card, if it is still in the tree.
function removeCard(card) {
    if (!card)
        return;
    const parent = card.get_parent();
    if (parent)
        parent.remove_child(card);
    card.destroy();
}

