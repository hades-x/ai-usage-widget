#!/usr/bin/env bash
# Headless GNOME Shell smoke test.
#
# MUST be run as a dedicated, unprivileged TEST USER that has no graphical login session.
# Reason: this script rewrites org.gnome.shell enabled-extensions / disable-user-extensions
# in the dconf database of the user running it. The shell itself runs on a private dbus
# session, but dconf is per-user on disk, so running it as your desktop user would change
# your real extension setup. The script backs up both keys and restores them on exit, but
# a test user is still the only safe setup.
#
# The script refuses to run inside a graphical session ($XDG_SESSION_TYPE is x11/wayland,
# or $DISPLAY / $WAYLAND_DISPLAY is set). Override with AIU_HEADLESS_FORCE=1 only if you
# know what you are doing.
#
# Installs the extension + a test-only helper that turns on unsafe mode, starts a headless shell,
# checks the extension state over D-Bus, runs eval probes and takes screenshots.
# Usage: tools/headless/run-headless.sh <repo_dir> <state.json> <out_dir>
# Output: <out_dir>/probes.txt, ext-info.txt, shell.log, prefs.txt, *.png, state-notify.json
set -euo pipefail
REPO=$(realpath "$1"); STATE=$(realpath "$2"); OUT=$(realpath -m "$3")
UUID=ai-usage@hades-x.github.io
HELPER=unsafe-helper@test.local
EXT_DIR="$HOME/.local/share/gnome-shell/extensions"

if [[ "${AIU_HEADLESS_FORCE:-0}" != "1" ]]; then
    case "${XDG_SESSION_TYPE:-}" in
        x11|wayland) echo "run-headless: refusing: XDG_SESSION_TYPE=${XDG_SESSION_TYPE} (graphical session). Use a dedicated test user, or AIU_HEADLESS_FORCE=1." >&2; exit 1 ;;
    esac
    if [[ -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ]]; then
        echo "run-headless: refusing: DISPLAY/WAYLAND_DISPLAY is set (graphical session). Use a dedicated test user, or AIU_HEADLESS_FORCE=1." >&2
        exit 1
    fi
fi

mkdir -p "$OUT" "$EXT_DIR"
# Isolation: the inner session gets its own XDG_CONFIG_HOME, so dconf writes go to a private
# ~/.config/dconf/user under $OUT and never touch the real user database. The real keys are
# still backed up, verified after the run, and restored if they ever differ.
INNER_XDG="$OUT/xdg-config"
BAK_ENABLED=$(dconf read /org/gnome/shell/enabled-extensions 2>/dev/null || true)
BAK_DISABLE=$(dconf read /org/gnome/shell/disable-user-extensions 2>/dev/null || true)
restore_dconf() {
    if [[ -n "$BAK_ENABLED" ]]; then
        dconf write /org/gnome/shell/enabled-extensions "$BAK_ENABLED"
    else
        dconf reset /org/gnome/shell/enabled-extensions
    fi
    if [[ -n "$BAK_DISABLE" ]]; then
        dconf write /org/gnome/shell/disable-user-extensions "$BAK_DISABLE"
    else
        dconf reset /org/gnome/shell/disable-user-extensions
    fi
}
verify_dconf() {
    local now_e now_d
    now_e=$(dconf read /org/gnome/shell/enabled-extensions 2>/dev/null || true)
    now_d=$(dconf read /org/gnome/shell/disable-user-extensions 2>/dev/null || true)
    [[ "$now_e" == "$BAK_ENABLED" && "$now_d" == "$BAK_DISABLE" ]]
}
cleanup() {
    rm -rf "$EXT_DIR/$UUID" "$EXT_DIR/$HELPER"
    if ! verify_dconf; then
        echo "run-headless: WARNING: user dconf keys changed during the run; restoring them" >&2
        restore_dconf
        verify_dconf || echo "run-headless: ERROR: could not restore user dconf keys" >&2
    fi
}
trap cleanup EXIT
cleanup
cp -r "$REPO/extension/$UUID" "$EXT_DIR/$UUID"
glib-compile-schemas "$EXT_DIR/$UUID/schemas"
cp -r "$REPO/tools/headless/$HELPER" "$EXT_DIR/$HELPER"

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
mkdir -p "$XDG_RUNTIME_DIR/ai-usage"
cp "$STATE" "$XDG_RUNTIME_DIR/ai-usage/state.json"

XDG_CONFIG_HOME="$INNER_XDG" dbus-run-session -- bash -s "$OUT" "$UUID" "$HELPER" "$REPO" "$EXT_DIR" "$STATE" <<'EOS'
set -u
OUT=$1; UUID=$2; HELPER=$3; REPO=$4; EXT_DIR=$5; STATE=$6
SCHEMADIR="$EXT_DIR/$UUID/schemas"
gsettings set org.gnome.shell disable-user-extensions false
gsettings set org.gnome.shell enabled-extensions "['$HELPER', '$UUID']"
rm -f "$XDG_RUNTIME_DIR/gnome-shell-disable-extensions"
# dedupe keys persisted by earlier runs must not leak into this one
gsettings --schemadir "$SCHEMADIR" reset org.gnome.shell.extensions.ai-usage notified-keys
gnome-shell --headless --virtual-monitor 1920x1080 --wayland --no-x11 >"$OUT/shell.log" 2>&1 &
PID=$!
for i in $(seq 1 30); do
  gdbus call --session -d org.gnome.Shell -o /org/gnome/Shell -m org.freedesktop.DBus.Properties.Get org.gnome.Shell ShellVersion >/dev/null 2>&1 && break
  sleep 1
done
sleep 4
gnome-extensions info "$UUID" > "$OUT/ext-info.txt" 2>&1
eval_js() { gdbus call --session -d org.gnome.Shell -o /org/gnome/Shell -m org.gnome.Shell.Eval "$1" 2>&1; }
shot() { gdbus call --session -d org.gnome.Shell -o /org/gnome/Shell/Screenshot -m org.gnome.Shell.Screenshot.Screenshot false false "$OUT/$1" >/dev/null 2>&1; }
run_probe() { echo "== $1"; eval_js "$(cat "$REPO/tools/headless/probes/$1")"; }
P="$OUT/probes.txt"
{
  echo "unsafe: $(eval_js 'global.context.unsafe_mode')"
  run_probe 10-indicator.js
} > "$P"
shot 00-desktop.png

# popup: open it with the real toggle (GNOME 43 refuses to open an empty menu)
eval_js "Main.overview.hide()" >/dev/null; sleep 1
eval_js "Main.panel.statusArea['ai-usage'].menu.toggle()" >/dev/null; sleep 2
shot 01-popup.png
run_probe 20-popup.js >> "$P"
eval_js "Main.panel.statusArea['ai-usage'].menu.close(false)" >/dev/null; sleep 1

# date-menu card; its click closes the date menu and opens the indicator popup
eval_js "Main.panel.statusArea.dateMenu.menu.toggle()" >/dev/null; sleep 2
shot 02-datemenu.png
run_probe 30-datemenu.js >> "$P"
run_probe 40-card-click.js >> "$P"
eval_js "Main.panel.statusArea.dateMenu.menu.close(false)" >/dev/null; sleep 1

# notification scenario: Claude five_hour at 85 % -> one 'warn' notification
python3 - "$STATE" "$OUT/state-notify.json" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))
for w in s["providers"]["claude"]["windows"]:
    if w["id"] == "five_hour":
        w["used_percent"] = 85.0
json.dump(s, open(sys.argv[2], "w"))
PY
echo "== notify: before state change" >> "$P"; run_probe 50-notify.js >> "$P"
cp "$OUT/state-notify.json" "$XDG_RUNTIME_DIR/ai-usage/state.json.tmp" && mv "$XDG_RUNTIME_DIR/ai-usage/state.json.tmp" "$XDG_RUNTIME_DIR/ai-usage/state.json"
sleep 3
echo "== notify: after 85% state" >> "$P"; run_probe 50-notify.js >> "$P"
# GNOME disables the extension on lock and re-enables it on unlock: no second notification
gnome-extensions disable "$UUID"; sleep 1
gnome-extensions enable "$UUID"; sleep 4
echo "== notify: after disable/enable" >> "$P"; run_probe 50-notify.js >> "$P"
# proof the state was reloaded after re-enable (chip shows the 85 % value) and no key was added
echo "== reload after re-enable (chip labels)" >> "$P"; run_probe 10-indicator.js >> "$P"
echo "notified-keys: $(gsettings --schemadir "$SCHEMADIR" get org.gnome.shell.extensions.ai-usage notified-keys)" >> "$P"

# disable/enable cycle: no leftover actors
gnome-extensions disable "$UUID"; sleep 1
echo "after disable: $(eval_js "String(Main.panel.statusArea['ai-usage'])")" >> "$P"
echo "after disable, card actors: $(eval_js "String(Main.panel.statusArea.dateMenu._displaysSection.get_child().get_children().filter(c => c.get_style_class_name && c.get_style_class_name().indexOf('ai-usage-card') >= 0).length)")" >> "$P"
gnome-extensions enable "$UUID"; sleep 2
echo "after re-enable: $(eval_js "String(!!Main.panel.statusArea['ai-usage'])")" >> "$P"
gnome-extensions info "$UUID" >> "$OUT/ext-info.txt" 2>&1

# prefs 1: the real entry point, D-Bus activated gnome-extensions-app (stderr of the activation goes to prefs-dbus.txt)
timeout 8 gnome-extensions prefs "$UUID" > "$OUT/prefs-dbus.txt" 2>&1 &
PREFS=$!
sleep 3
echo "prefs (gnome-extensions prefs) cli alive after 3 s: $(kill -0 $PREFS 2>/dev/null && echo yes || echo no)" >> "$OUT/prefs-dbus.txt"
pkill -f "gnome-extensions-app|ai-usage@hades-x.github.io/prefs" 2>/dev/null || true
wait $PREFS 2>/dev/null || true
# prefs 2: build the window from prefs.js on the headless Wayland display, run 3 s, report errors
WL=$(ls "$XDG_RUNTIME_DIR" | grep -E '^wayland-[0-9]+$' | head -1 || true)
echo "wayland socket: ${WL:-none}" > "$OUT/prefs.txt"
WAYLAND_DISPLAY="$WL" GDK_BACKEND=wayland timeout 30 gjs "$REPO/tools/headless/prefs-runner.js" "$EXT_DIR/$UUID" 3 >> "$OUT/prefs.txt" 2>&1
echo "prefs-runner exit: $?" >> "$OUT/prefs.txt"

kill $PID; wait $PID 2>/dev/null
gsettings --schemadir "$SCHEMADIR" reset org.gnome.shell.extensions.ai-usage notified-keys 2>/dev/null || true
true
EOS
grep -nE "JS ERROR|JS WARNING|ai-usage|CRITICAL|Gjs-WARNING|Extension" "$OUT/shell.log" | grep -v "^$" > "$OUT/shell-errors.txt" || true
echo "--- ext-info"; cat "$OUT/ext-info.txt"; echo "--- probes"; cat "$OUT/probes.txt"; echo "--- prefs"; cat "$OUT/prefs.txt" 2>/dev/null; echo "--- shell log matches"; cat "$OUT/shell-errors.txt"; ls -la "$OUT"
