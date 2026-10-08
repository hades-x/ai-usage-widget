#!/usr/bin/env bash
# Remove the AI usage collector: timer, service units, installed package and cache.
set -euo pipefail

if [[ "$(id -u)" -eq 0 ]]; then
    echo "uninstall-collector: refusing to run as root; run as your desktop user." >&2
    exit 1
fi

UNIT_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
DATA_DIR="${HOME}/.local/share/ai-usage"
UNITS=(ai-usage-collector.timer ai-usage-collector.service)

echo "==> stopping and disabling the timer"
systemctl --user disable --now ai-usage-collector.timer 2>/dev/null || true
systemctl --user stop ai-usage-collector.service 2>/dev/null || true

echo "==> removing units from ${UNIT_DIR}"
for unit in "${UNITS[@]}"; do
    rm -f "${UNIT_DIR}/${unit}"
done
systemctl --user daemon-reload
systemctl --user reset-failed ai-usage-collector.service 2>/dev/null || true

echo "==> removing ${DATA_DIR}"
rm -rf "${DATA_DIR}"

# Runtime state lives in $XDG_RUNTIME_DIR/ai-usage and is removed here as well.
RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/ai-usage"
rm -rf "${RUNTIME_DIR}"
CACHE_DIR="${XDG_CACHE_HOME:-${HOME}/.cache}/ai-usage"
rm -rf "${CACHE_DIR}"

echo "Uninstalled."
