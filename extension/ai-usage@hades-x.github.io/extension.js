// -*- mode: js; js-indent-level: 4; indent-tabs-mode: nil -*-
// AI Usage — GNOME Shell 43 extension (legacy API). Wiring only; logic lives in lib/.
/* exported init */

const { Gio, GLib } = imports.gi;
const Main = imports.ui.main;
const ExtensionUtils = imports.misc.extensionUtils;
const Me = ExtensionUtils.getCurrentExtension();

const Format = Me.imports.lib.format;
const StateLoaderMod = Me.imports.lib.stateLoader;
const IndicatorMod = Me.imports.lib.indicator;
const CardMod = Me.imports.lib.dateMenuCard;
const NotifierMod = Me.imports.lib.notifier;

const TICK_S = 30;

class Extension {
    constructor() {
        this._uuid = Me.metadata.uuid;
        this._path = Me.path;
        this._settings = null;
        this._settingsIds = [];
        this._indicator = null;
        this._indicatorAdded = false;
        this._card = null;
        this._notifier = null;
        this._loader = null;
        this._tickId = 0;
        this._state = null;
        this._status = 'missing';
    }

    enable() {
        this._settings = ExtensionUtils.getSettings();
        this._notifier = new NotifierMod.Notifier(this._path, this._settings);

        this._loader = new StateLoaderMod.StateLoader(
            () => this._settings.get_string('state-path'),
            result => this._onState(result));

        this._settingsIds.push(this._settings.connect('changed::panel-position', () => this._applyPanel()));
        this._settingsIds.push(this._settings.connect('changed::show-in-panel', () => this._applyPanel()));
        this._settingsIds.push(this._settings.connect('changed::show-in-datemenu', () => this._applyCard()));
        this._settingsIds.push(this._settings.connect('changed::panel-mode', () => this._refreshAll()));
        this._settingsIds.push(this._settings.connect('changed::show-claude', () => this._refreshAll()));
        this._settingsIds.push(this._settings.connect('changed::show-codex', () => this._refreshAll()));
        this._settingsIds.push(this._settings.connect('changed::warn-threshold', () => this._refreshAll()));
        this._settingsIds.push(this._settings.connect('changed::crit-threshold', () => this._refreshAll()));
        this._settingsIds.push(this._settings.connect('changed::state-path', () => this._loader.restart()));

        this._applyPanel();
        this._applyCard();

        this._tickId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, TICK_S, () => {
            this._tick();
            return GLib.SOURCE_CONTINUE;
        });

        this._loader.start();
    }

    disable() {
        // Stop producers first so no callback runs against half-destroyed actors.
        if (this._tickId) {
            GLib.source_remove(this._tickId);
            this._tickId = 0;
        }
        if (this._loader) {
            this._loader.stop();
            this._loader = null;
        }
        if (this._settings) {
            for (const id of this._settingsIds)
                this._settings.disconnect(id);
        }
        this._settingsIds = [];

        this._removeCard();
        this._removeIndicator();

        if (this._notifier) {
            this._notifier.destroy();
            this._notifier = null;
        }

        this._settings = null;
        this._state = null;
        this._status = 'missing';
    }

    // ---------------------------------------------------------------- state
    _onState(result) {
        if (!this._settings)
            return;
        this._status = result.status;
        this._state = result.state;
        if (result.status === 'ok' && this._notifier)
            this._notifier.check(this._state);
        this._refreshAll();
    }

    _tick() {
        if (this._indicator)
            this._indicator.tick();
        if (this._card && this._state)
            this._card.render(this._state, this._settings, Date.now());
    }

    _refreshAll() {
        this._refreshIndicator();
        this._refreshCard();
    }

    // ---------------------------------------------------------------- panel
    _applyPanel() {
        const show = this._settings.get_boolean('show-in-panel');
        if (!show) {
            this._removeIndicator();
            return;
        }
        if (!this._indicator) {
            this._indicator = new IndicatorMod.ProviderIndicator({
                settings: this._settings,
                extensionPath: this._path,
                openPrefs: () => ExtensionUtils.openPrefs(),
            });
        }
        this._placeIndicator();
        this._refreshIndicator();
    }

    // Position: left / center / right (index 0 = before the system menu, DESIGN §1).
    _placeIndicator() {
        const pos = this._settings.get_string('panel-position');
        const box = pos === 'left' ? 'left' : (pos === 'center' ? 'center' : 'right');
        if (this._indicatorAdded)
            this._removeIndicatorFromPanel();
        // Remove any indicator with the same role left by a previous instance.
        Main.panel.addToStatusArea(IndicatorMod.ROLE, this._indicator, 0, box);
        this._indicatorAdded = true;
    }

    // Detaches the indicator's container from the panel box without destroying it.
    // PanelMenu.Button.container is the St.Bin that the panel box holds (see panel.js).
    _removeIndicatorFromPanel() {
        if (this._indicator && Main.panel.statusArea[IndicatorMod.ROLE] === this._indicator)
            delete Main.panel.statusArea[IndicatorMod.ROLE];
        if (this._indicator) {
            const container = this._indicator.container;
            const parent = container.get_parent();
            if (parent)
                parent.remove_child(container);
        }
        this._indicatorAdded = false;
    }

    _refreshIndicator() {
        if (!this._indicator)
            return;
        this._indicator.setState({ status: this._status, state: this._state });
    }

    _removeIndicator() {
        if (!this._indicator)
            return;
        this._removeIndicatorFromPanel();
        this._indicator.destroy();
        this._indicator = null;
    }

    // ----------------------------------------------------------------- card
    _applyCard() {
        const show = this._settings.get_boolean('show-in-datemenu');
        if (!show) {
            this._removeCard();
            return;
        }
        if (this._card)
            return;
        const dateMenu = Main.panel.statusArea.dateMenu;
        if (!dateMenu)
            return;
        const card = new CardMod.DateMenuCard({
            extensionPath: this._path,
            onClick: () => this._onCardClicked(),
        });
        if (CardMod.insertCard(dateMenu, card)) {
            this._card = card;
            this._refreshCard();
        } else {
            card.destroy();
        }
    }

    _refreshCard() {
        if (this._card)
            this._card.render(this._state, this._settings, Date.now());
    }

    _removeCard() {
        if (!this._card)
            return;
        CardMod.removeCard(this._card);
        this._card = null;
    }

    _onCardClicked() {
        // DESIGN §3: close the date menu, open the popup of the indicator, or prefs if hidden.
        Main.panel.closeCalendar();
        if (this._indicator && this._settings.get_boolean('show-in-panel'))
            this._indicator.menu.open();
        else
            ExtensionUtils.openPrefs();
    }
}

function init() {
    return new Extension();
}
