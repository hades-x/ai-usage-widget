#!/usr/bin/env bash
# Install the AI usage collector as a systemd --user timer (run as the desktop user, not root).
set -euo pipefail

if [[ "$(id -u)" -eq 0 ]]; then
    echo "install-collector: refusing to run as root; run as your desktop user." >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_PKG="${REPO_DIR}/collector/ai_usage"
SRC_UNITS="${REPO_DIR}/systemd"
DEST_ROOT="${HOME}/.local/share/ai-usage/collector"
DEST_PKG="${DEST_ROOT}/ai_usage"
UNIT_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
UNITS=(ai-usage-collector.service ai-usage-collector.timer)

for path in "${SRC_PKG}" "${SRC_UNITS}/${UNITS[0]}" "${SRC_UNITS}/${UNITS[1]}"; do
    if [[ ! -e "${path}" ]]; then
        echo "install-collector: missing source ${path}" >&2
        exit 1
    fi
done

echo "==> copying package to ${DEST_PKG}"
mkdir -p "${DEST_ROOT}"
chmod 700 "${HOME}/.local/share/ai-usage" "${DEST_ROOT}" 2>/dev/null || true
rm -rf "${DEST_PKG}"
cp -r "${SRC_PKG}" "${DEST_PKG}"
find "${DEST_PKG}" -name '__pycache__' -type d -prune -exec rm -rf {} +

echo "==> installing units to ${UNIT_DIR}"
mkdir -p "${UNIT_DIR}"
for unit in "${UNITS[@]}"; do
    install -m 0644 "${SRC_UNITS}/${unit}" "${UNIT_DIR}/${unit}"
done

echo "==> reloading user systemd and enabling the timer"
systemctl --user daemon-reload
systemctl --user enable --now ai-usage-collector.timer
echo "==> running the collector once"
systemctl --user start ai-usage-collector.service || {
    echo "install-collector: first run failed; see: journalctl --user -u ai-usage-collector -n 50" >&2
    exit 1
}

echo
echo "Installed. Useful commands:"
echo "  systemctl --user status ai-usage-collector.timer"
echo "  journalctl --user -u ai-usage-collector -n 20"
echo "  python3 -m ai_usage doctor   (with PYTHONPATH=${DEST_ROOT})"
echo "  PYTHONPATH=${DEST_ROOT} python3 -m ai_usage print"
