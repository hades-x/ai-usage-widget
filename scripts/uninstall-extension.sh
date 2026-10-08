#!/usr/bin/env bash
# Remove the AI Usage GNOME Shell extension for the current user.
set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then
    echo "uninstall-extension: refuse to run as root (uninstall as your desktop user)" >&2
    exit 1
fi

UUID="ai-usage@hades-x.github.io"
DEST_BASE="${XDG_DATA_HOME:-${HOME}/.local/share}/gnome-shell/extensions"
DEST="${DEST_BASE}/${UUID}"

# Also remove the pre-release UUID (ai-usage@hades.local) if it is still installed.
LEGACY_UUID="ai-usage@hades.local"
UUIDS=("${UUID}" "${LEGACY_UUID}")

for u in "${UUIDS[@]}"; do
    if command -v gnome-extensions >/dev/null 2>&1; then
        gnome-extensions disable "${u}" >/dev/null 2>&1 || true
    fi
    d="${DEST_BASE}/${u}"
    if [[ -d "${d}" ]]; then
        rm -rf "${d}"
        echo "Removed ${d}"
    else
        echo "Nothing to remove: ${d} does not exist"
    fi
done
