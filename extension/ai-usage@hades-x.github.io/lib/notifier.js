// -*- mode: js; js-indent-level: 4; indent-tabs-mode: nil -*-
/* exported Notifier */
// Threshold notifications through the GNOME 43 message tray (DESIGN §4).
// Source / Notification API: js/ui/messageTray.js of 43.9:
//   new MessageTray.Source(title, iconName)  -> Source._init(title, iconName)
//   Main.messageTray.add(source)              -> MessageTray.add(source)
//   new MessageTray.Notification(source, title, banner, params)  (params.gicon)
//   notification.setUrgency(Urgency.NORMAL|HIGH)
//   source.showNotification(notification)
//   source.destroy()                          -> destroys notifications, emits 'destroy'
// Dedupe keys are persisted in the 'notified-keys' GSettings key (50 most recent), so
// a disable/enable cycle (GNOME disables extensions on lock) does not notify again.

const { Gio, GObject } = imports.gi;
const Main = imports.ui.main;
const MessageTray = imports.ui.messageTray;
const ExtensionUtils = imports.misc.extensionUtils;

const Me = ExtensionUtils.getCurrentExtension();
const Format = Me.imports.lib.format;

const SOURCE_TITLE = 'Consommation IA';
const MAX_KEYS = 50;

var IconSource = GObject.registerClass(
class IconSource extends MessageTray.Source {
    _init(title, iconFile) {
        super._init(title, 'dialog-information-symbolic');
        this._iconFile = iconFile;
    }

    getIcon() {
        return Gio.icon_new_for_string(this._iconFile);
    }
});

var Notifier = class {
    // settings: Gio.Settings of the extension (provides 'notified-keys').
    constructor(extensionPath, settings) {
        this._path = extensionPath;
        this._settings = settings;
        this._source = null;
        // key -> true, insertion ordered (oldest first); loaded from GSettings.
        this._fired = new Map(settings.get_strv('notified-keys').map(k => [k, true]));
        this._destroyed = false;
    }

    _ensureSource() {
        if (this._source)
            return this._source;
        if (!Main.messageTray)
            return null;
        // The shell icon used for the whole source; provider icons go on each notification.
        this._source = new IconSource(SOURCE_TITLE, `${this._path}/icons/claude-symbolic.svg`);
        Main.messageTray.add(this._source);
        this._source.connect('destroy', () => {
            this._source = null;
        });
        return this._source;
    }

    _remember(key) {
        this._fired.delete(key);
        this._fired.set(key, true);
        while (this._fired.size > MAX_KEYS) {
            const oldest = this._fired.keys().next().value;
            this._fired.delete(oldest);
        }
        this._settings.set_strv('notified-keys', [...this._fired.keys()]);
    }

    // state: parsed state.json, status 'ok' only.
    check(state) {
        if (this._destroyed || !state || !state.providers)
            return;
        const settings = this._settings;
        if (!settings.get_boolean('notify-enabled'))
            return;
        const warn = settings.get_int('warn-threshold');
        const crit = settings.get_int('crit-threshold');

        for (const id of ['claude', 'codex']) {
            const p = state.providers[id];
            if (!p || p.available === false || !Array.isArray(p.windows))
                continue;
            for (const win of p.windows) {
                if (!win || typeof win.used_percent !== 'number' || win.reset_since_observation)
                    continue;
                const level = Format.windowLevel(win.used_percent, warn, crit);
                if (level === 'normal')
                    continue;
                const key = Format.notificationKey(id, win, level);
                if (this._fired.has(key))
                    continue;
                this._remember(key);
                this._send(p, win, level);
            }
        }
    }

    _send(provider, win, level) {
        const source = this._ensureSource();
        if (!source)
            return;
        const title = provider.label || provider.id;
        const body = Format.notificationText(title, win, level, win.resets_at);
        const notification = new MessageTray.Notification(
            source,
            SOURCE_TITLE,
            body,
            {
                gicon: Gio.icon_new_for_string(`${this._path}/icons/${provider.id === 'codex' ? 'codex' : 'claude'}-symbolic.svg`),
            });
        notification.setUrgency(level === 'crit' ? MessageTray.Urgency.HIGH : MessageTray.Urgency.NORMAL);
        notification.setTransient(false);
        source.showNotification(notification);
    }

    // Releases the tray source (its notifications are destroyed with it). The persisted
    // dedupe keys are kept on purpose: they must survive disable/enable.
    destroy() {
        this._destroyed = true;
        if (this._source) {
            const source = this._source;
            this._source = null;
            source.destroy();
        }
        this._fired.clear();
    }
};
