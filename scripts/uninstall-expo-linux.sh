#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_DIR="/srv/StatusVisualizer"
DATA_ROOT="$INSTALL_DIR/data"
ENV_DIR="/etc/status-visualizer"
purge_data="false"

if [ "${1:-}" = "--purge-data" ]; then
    purge_data="true"
elif [ "$#" -gt 0 ]; then
    printf 'Usage: sudo bash scripts/uninstall-expo-linux.sh [--purge-data]\n' >&2
    exit 1
fi
if [ "$(id -u)" -ne 0 ]; then
    printf 'Error: run this uninstaller as root.\n' >&2
    exit 1
fi

for service in \
    status-visualizer-launcher.service \
    status-visualizer@flat.service \
    status-visualizer@compartmentalized.service \
    status-visualizer@live.service; do
    systemctl disable --now "$service" >/dev/null 2>&1 || true
done
rm -f -- \
    /etc/systemd/system/status-visualizer-launcher.service \
    /etc/systemd/system/status-visualizer@.service \
    "$ENV_DIR/flat.env" \
    "$ENV_DIR/compartmentalized.env" \
    "$ENV_DIR/live.env"
rmdir --ignore-fail-on-non-empty "$ENV_DIR" 2>/dev/null || true
systemctl daemon-reload

if [ "$purge_data" = "true" ]; then
    if [ "$DATA_ROOT" != "/srv/StatusVisualizer/data" ]; then
        printf 'Refusing to purge unexpected data path: %s\n' "$DATA_ROOT" >&2
        exit 1
    fi
    if [ -L "$DATA_ROOT" ]; then
        printf 'Refusing to purge symlinked data path: %s\n' "$DATA_ROOT" >&2
        exit 1
    fi
    if [ -d "$DATA_ROOT" ]; then
        rm -rf -- "$DATA_ROOT"
        printf 'Expo topology data was permanently removed. Backups were preserved.\n'
    else
        printf 'Expo topology data directory was already absent.\n'
    fi
else
    printf 'Expo topology data was kept under %s.\n' "$DATA_ROOT"
fi
printf 'The legacy status-visualizer.service was not changed or restarted.\n'
