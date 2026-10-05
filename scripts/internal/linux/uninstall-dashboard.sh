#!/usr/bin/env bash
set -Eeuo pipefail

[ "$(id -u)" -eq 0 ] || { printf 'Run with sudo.\n' >&2; exit 1; }

systemctl disable --now status-visualizer.service 2>/dev/null || true
rm -f /etc/systemd/system/status-visualizer.service
systemctl daemon-reload
rm -rf /opt/status-visualizer

printf 'Status Visualizer was removed.\n'
printf 'Data and MQTT credentials were preserved under /var/lib/status-visualizer and /etc/status-visualizer.\n'
