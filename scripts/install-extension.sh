#!/usr/bin/env bash
# Install the AI Usage GNOME Shell extension for the current user.
# Idempotent: replaces any previous copy. Refuses to run as root.
set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then
    echo "install-extension: refuse to run as root (install as your desktop user)" >&2
    exit 1
fi

UUID="ai-usage@hades-x.github.io"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${ROOT}/extension/${UUID}"
DEST_BASE="${XDG_DATA_HOME:-${HOME}/.local/share}/gnome-shell/extensions"
DEST="${DEST_BASE}/${UUID}"

if [[ ! -f "${SRC}/metadata.json" ]]; then
    echo "install-extension: missing ${SRC}/metadata.json" >&2
    exit 1
fi

if ! command -v glib-compile-schemas >/dev/null 2>&1; then
    echo "install-extension: glib-compile-schemas not found (install libglib2.0-bin)" >&2
    exit 1
fi

# Migrate from the pre-release UUID (ai-usage@hades.local): disable and remove it.
LEGACY_UUID="ai-usage@hades.local"
LEGACY_DEST="${DEST_BASE}/${LEGACY_UUID}"
if [[ -e "${LEGACY_DEST}" ]]; then
    if command -v gnome-extensions >/dev/null 2>&1; then
        gnome-extensions disable "${LEGACY_UUID}" >/dev/null 2>&1 || true
    fi
    rm -rf "${LEGACY_DEST}"
    echo "Note: removed legacy extension ${LEGACY_UUID} (renamed to ${UUID})."
fi

mkdir -p "${DEST_BASE}"
rm -rf "${DEST}"
cp -r "${SRC}" "${DEST}"

# The compiled schema is generated here, never committed.
glib-compile-schemas "${DEST}/schemas"

echo "Installed ${UUID} into ${DEST}"

if command -v gnome-extensions >/dev/null 2>&1; then
    gnome-extensions enable "${UUID}" >/dev/null 2>&1 || true
fi

cat <<MSG
X11: Alt+F2, r, Enter then: gnome-extensions enable ${UUID}
(Changes to the extension code only take effect after the shell is reloaded.)
MSG
