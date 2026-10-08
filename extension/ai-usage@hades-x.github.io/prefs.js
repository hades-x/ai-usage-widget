// -*- mode: js; js-indent-level: 4; indent-tabs-mode: nil -*-
// Preferences window (GNOME 43: GTK 4 + libadwaita 1.2). DESIGN §5.
// Only widgets available in libadwaita 1.2 are used (no SwitchRow / SpinRow, which are 1.4+).
// Legacy API: no ES modules. Runs in the prefs process, not in gnome-shell.

imports.gi.versions.Gtk = '4.0';
imports.gi.versions.Adw = '1';

const { Adw, Gtk, Gio } = imports.gi;
const ExtensionUtils = imports.misc.extensionUtils;

function init() {
}

function buildSwitchRow(settings, key, title, subtitle) {
    const row = new Adw.ActionRow({ title, subtitle: subtitle || null });
    const sw = new Gtk.Switch({ valign: Gtk.Align.CENTER, active: settings.get_boolean(key) });
    settings.bind(key, sw, 'active', Gio.SettingsBindFlags.DEFAULT);
    row.add_suffix(sw);
    row.activatable_widget = sw;
    return row;
}

function buildSpinRow(settings, key, title, subtitle, min, max) {
    const row = new Adw.ActionRow({ title, subtitle: subtitle || null });
    const adj = new Gtk.Adjustment({
        lower: min,
        upper: max,
        step_increment: 1,
        page_increment: 5,
        value: settings.get_int(key),
    });
    const spin = new Gtk.SpinButton({ adjustment: adj, digits: 0, valign: Gtk.Align.CENTER });
    settings.bind(key, spin, 'value', Gio.SettingsBindFlags.DEFAULT);
    row.add_suffix(spin);
    row.activatable_widget = spin;
    return row;
}

// Combo over a fixed list of [value, label]; stored as a string key.
function buildComboRow(settings, key, title, subtitle, options) {
    const row = new Adw.ActionRow({ title, subtitle: subtitle || null });
    const model = new Gtk.StringList();
    for (const [, label] of options)
        model.append(label);
    const combo = new Gtk.DropDown({ model, valign: Gtk.Align.CENTER });
    const current = settings.get_string(key);
    const idx = Math.max(0, options.findIndex(([value]) => value === current));
    combo.set_selected(idx);
    combo.connect('notify::selected', () => {
        const sel = combo.get_selected();
        if (sel >= 0 && sel < options.length)
            settings.set_string(key, options[sel][0]);
    });
    row.add_suffix(combo);
    row.activatable_widget = combo;
    return row;
}

function buildEntryRow(settings, key, title, subtitle) {
    const row = new Adw.ActionRow({ title, subtitle: subtitle || null });
    const entry = new Gtk.Entry({
        text: settings.get_string(key),
        placeholder_text: '$XDG_RUNTIME_DIR/ai-usage/state.json',
        valign: Gtk.Align.CENTER,
        width_chars: 34,
    });
    // Commit on Enter / focus-out only, so a half-typed path is not used on every keystroke.
    const commit = () => {
        const v = entry.get_text().trim();
        if (v !== settings.get_string(key))
            settings.set_string(key, v);
    };
    entry.connect('activate', commit);
    entry.connect('notify::has-focus', () => {
        if (!entry.has_focus)
            commit();
    });
    row.add_suffix(entry);
    return row;
}

function fillPreferencesWindow(window) {
    const settings = ExtensionUtils.getSettings();

    // ---- Page: Général
    const general = new Adw.PreferencesPage({
        title: 'Général',
        icon_name: 'preferences-system-symbolic',
    });

    const place = new Adw.PreferencesGroup({ title: 'Barre supérieure' });
    place.add(buildComboRow(settings, 'panel-position', 'Position',
        'Emplacement de l’indicateur dans la barre', [
            ['left', 'Gauche'],
            ['center', 'Centre'],
            ['right', 'Droite'],
        ]));
    place.add(buildComboRow(settings, 'panel-mode', 'Affichage',
        'Pourcentage du quota, tokens du jour, ou les deux', [
            ['percent', 'Pourcentage'],
            ['tokens', 'Tokens du jour'],
            ['both', 'Les deux'],
        ]));
    place.add(buildSwitchRow(settings, 'show-in-panel', 'Afficher dans la barre supérieure'));
    place.add(buildSwitchRow(settings, 'show-in-datemenu', 'Afficher dans le menu horloge',
        'Carte sous la météo, dans la colonne de droite'));
    general.add(place);

    const providers = new Adw.PreferencesGroup({ title: 'Fournisseurs' });
    providers.add(buildSwitchRow(settings, 'show-claude', 'Claude Code'));
    providers.add(buildSwitchRow(settings, 'show-codex', 'Codex'));
    general.add(providers);

    // ---- Page: Alertes
    const alerts = new Adw.PreferencesPage({
        title: 'Alertes',
        icon_name: 'preferences-system-notifications-symbolic',
    });
    const notif = new Adw.PreferencesGroup({ title: 'Notifications' });
    notif.add(buildSwitchRow(settings, 'notify-enabled', 'Notifier les seuils',
        'Une notification par fenêtre et par niveau franchi'));
    alerts.add(notif);

    const thresholds = new Adw.PreferencesGroup({ title: 'Seuils' });
    thresholds.add(buildSpinRow(settings, 'warn-threshold', 'Avertissement (%)', null, 1, 100));
    thresholds.add(buildSpinRow(settings, 'crit-threshold', 'Critique (%)', null, 1, 100));
    alerts.add(thresholds);

    // ---- Page: Avancé
    const advanced = new Adw.PreferencesPage({
        title: 'Avancé',
        icon_name: 'applications-engineering-symbolic',
    });
    const source = new Adw.PreferencesGroup({ title: 'Source' });
    source.add(buildEntryRow(settings, 'state-path', 'Chemin de state.json',
        'Vide = emplacement par défaut (tmpfs de la session)'));
    advanced.add(source);

    const logs = new Adw.PreferencesGroup({
        title: 'Diagnostic',
        description: 'journalctl --user -u ai-usage-collector  ·  journalctl _COMM=gnome-shell | grep ai-usage',
    });
    advanced.add(logs);

    window.add(general);
    window.add(alerts);
    window.add(advanced);
}
