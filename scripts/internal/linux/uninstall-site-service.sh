#!/usr/bin/env bash
set -Eeuo pipefail

[ "$(id -u)" -eq 0 ] || { printf 'Run with sudo.\n' >&2; exit 1; }

systemctl disable --now status-visualizer-mqtt-helper.service 2>/dev/null || true
rm -f /etc/systemd/system/status-visualizer-mqtt-helper.service
systemctl daemon-reload
rm -rf /opt/status-visualizer-mqtt-helper

printf 'LanTopoLog MQTT service was removed.\n'
printf 'Identity, publish state, config, and MQTT credentials were preserved under /var/lib/status-visualizer-mqtt-helper and /etc/status-visualizer-mqtt-helper.\n'
