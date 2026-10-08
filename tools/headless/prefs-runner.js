// Headless prefs check: loads the extension's prefs.js under GJS 1.74 with GTK4/Adw, builds the
// preferences window through fillPreferencesWindow() and runs the GLib main loop for a few seconds.
// Usage: WAYLAND_DISPLAY=<socket> GDK_BACKEND=wayland gjs prefs-runner.js <extension-dir> <seconds>
// Prints JSON: {window, pages, groups, rows, errors}. Any exception is reported and exits 1.
imports.gi.versions.Gtk = '4.0';
imports.gi.versions.Adw = '1';
const { Gio, GLib, Adw, Gtk } = imports.gi;

const extDir = ARGV[0];
const seconds = Number(ARGV[1] || 3);
const errors = [];

// Stand-in for imports.misc.extensionUtils: the only thing prefs.js needs is getSettings().
const schemaSource = Gio.SettingsSchemaSource.new_from_directory(
    `${extDir}/schemas`, Gio.SettingsSchemaSource.get_default(), false);
const schema = schemaSource.lookup('org.gnome.shell.extensions.ai-usage', true);
const settings = new Gio.Settings({ settings_schema: schema });
const ExtensionUtils = { getSettings: () => settings };

const source = imports.byteArray.toString(Gio.File.new_for_path(`${extDir}/prefs.js`).load_contents(null)[1]);
// prefs.js takes ExtensionUtils from imports.misc.extensionUtils; swap that for the stub above.
const patched = source.replace('const ExtensionUtils = imports.misc.extensionUtils;', 'const ExtensionUtils = __stub;');
if (patched === source) {
    print('ERROR: prefs.js import line not found');
    imports.system.exit(1);
}
const mod = new Function('__stub', `${patched}\nreturn { init, fillPreferencesWindow };`)(ExtensionUtils);

Adw.init();
mod.init();
const win = new Adw.PreferencesWindow();
const counts = { pages: 0, groups: 0, rows: 0 };
try {
    mod.fillPreferencesWindow(win);
    win.present();
} catch (e) {
    errors.push(`fillPreferencesWindow: ${e.message}`);
}
const walk = (w) => {
    if (w instanceof Adw.PreferencesPage) counts.pages++;
    if (w instanceof Adw.PreferencesGroup) counts.groups++;
    if (w instanceof Adw.ActionRow) counts.rows++;
    let c = w.get_first_child ? w.get_first_child() : null;
    while (c) { walk(c); c = c.get_next_sibling(); }
};
const loop = GLib.MainLoop.new(null, false);
GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, seconds, () => { loop.quit(); return GLib.SOURCE_REMOVE; });
loop.run();
// Adw.PreferencesWindow holds its pages in the window's own tree; walk it after presenting.
walk(win);
print(JSON.stringify({ window: 'present', ...counts, errors }));
imports.system.exit(errors.length ? 1 : 0);
