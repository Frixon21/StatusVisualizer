#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_DIR="/srv/StatusVisualizer"
DATA_ROOT="$INSTALL_DIR/data"
BACKUP_ROOT="$INSTALL_DIR/backups"
ENV_DIR="/etc/status-visualizer"
LEGACY_DATABASE="$INSTALL_DIR/data/status.db"
SERVICE_USER="status-visualizer"
SERVICE_GROUP="status-visualizer"
instances="flat compartmentalized live"
cutover_started="false"
legacy_service_exists="false"
legacy_was_active="false"
legacy_was_enabled="false"
legacy_backup_dir=""

fail() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

if [ "$(id -u)" -ne 0 ]; then
    fail "Run this installer as root, for example with sudo."
fi

for command_name in python3 systemctl install cp date getent mv sha256sum; do
    command -v "$command_name" >/dev/null 2>&1 || fail "Missing required command: $command_name"
done

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
for required_path in \
    "$project_dir/expo_launcher.py" \
    "$project_dir/expo-launcher/index.html" \
    "$project_dir/expo-launcher/networks.json" \
    "$project_dir/scripts/status-visualizer@.service.template" \
    "$project_dir/scripts/status-visualizer-launcher.service.template" \
    "$INSTALL_DIR/run.py" \
    "$INSTALL_DIR/.venv/bin/python"; do
    [ -e "$required_path" ] || fail "Required file is missing: $required_path"
done
getent passwd "$SERVICE_USER" >/dev/null || fail "Service user $SERVICE_USER does not exist; run install-linux.sh first"

backup_legacy_database() {
    [ -f "$LEGACY_DATABASE" ] || return 0
    "$INSTALL_DIR/.venv/bin/python" - "$LEGACY_DATABASE" "$BACKUP_ROOT" <<'PY'
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
import sys

source = Path(sys.argv[1])
backup_root = Path(sys.argv[2])
timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
destination_dir = backup_root / timestamp
suffix = 1
while destination_dir.exists():
    destination_dir = backup_root / f"{timestamp}-{suffix}"
    suffix += 1
destination_dir.mkdir(parents=True, mode=0o700)
destination = destination_dir / "status.db"
with sqlite3.connect(source) as source_connection:
    with sqlite3.connect(destination) as destination_connection:
        source_connection.backup(destination_connection)
digest = hashlib.sha256(destination.read_bytes()).hexdigest()
(destination_dir / "status.db.sha256").write_text(f"{digest}  status.db\n", encoding="ascii")
print(destination_dir)
PY
}

retire_legacy_database() {
    [ -f "$LEGACY_DATABASE" ] || return 0
    destination_dir="$legacy_backup_dir"
    if [ -z "$destination_dir" ]; then
        timestamp="$(date -u +%Y%m%d-%H%M%SZ)"
        destination_dir="$BACKUP_ROOT/retired-$timestamp"
        suffix=1
        while [ -e "$destination_dir" ]; do
            destination_dir="$BACKUP_ROOT/retired-$timestamp-$suffix"
            suffix=$((suffix + 1))
        done
        install -d -o root -g root -m 0700 "$destination_dir"
    fi
    retired_database="$destination_dir/status.db.retired-from-data"
    suffix=1
    while [ -e "$retired_database" ]; do
        retired_database="$destination_dir/status.db.retired-from-data-$suffix"
        suffix=$((suffix + 1))
    done
    mv -- "$LEGACY_DATABASE" "$retired_database"
    chown root:root "$retired_database"
    chmod 0600 "$retired_database"
    sha256sum "$retired_database" > "$retired_database.sha256"
    chmod 0600 "$retired_database.sha256"
    printf 'Retired legacy database from %s to %s.\n' "$LEGACY_DATABASE" "$retired_database"
}

render_unit() {
    source_template="$1"
    destination="$2"
    "$INSTALL_DIR/.venv/bin/python" - "$source_template" "$destination" "$INSTALL_DIR" "$DATA_ROOT" "$ENV_DIR" <<'PY'
from pathlib import Path
import sys

source, destination, install_dir, data_root, env_dir = sys.argv[1:]
unit = Path(source).read_text(encoding="utf-8")
unit = unit.replace("@@INSTALL_DIR@@", install_dir)
unit = unit.replace("@@DATA_ROOT@@", data_root)
unit = unit.replace("@@ENV_DIR@@", env_dir)
Path(destination).write_text(unit, encoding="utf-8")
PY
    chown root:root "$destination"
    chmod 0644 "$destination"
}

wait_for_health() {
    service_name="$1"
    health_url="$2"
    attempt=1
    while [ "$attempt" -le 40 ]; do
        if "$INSTALL_DIR/.venv/bin/python" -c \
            "import json, urllib.request; data=json.load(urllib.request.urlopen('$health_url', timeout=1)); raise SystemExit(0 if data.get('status') == 'ok' else 1)" \
            >/dev/null 2>&1; then
            return 0
        fi
        sleep 0.5
        attempt=$((attempt + 1))
    done
    journalctl --unit "$service_name" --lines 50 --no-pager >&2 || true
    return 1
}

rollback_cutover() {
    [ "$cutover_started" = "true" ] || return 0
    printf 'Launcher cutover failed; restoring the legacy service on port 6042.\n' >&2
    systemctl disable --now status-visualizer-launcher.service >/dev/null 2>&1 || true
    if [ "$legacy_service_exists" = "true" ]; then
        if [ "$legacy_was_enabled" = "true" ]; then
            systemctl enable status-visualizer.service >/dev/null 2>&1 || true
        fi
        if [ "$legacy_was_active" = "true" ]; then
            systemctl start status-visualizer.service >/dev/null 2>&1 || true
        fi
    fi
}

trap rollback_cutover ERR

# Capture a transactionally consistent copy without stopping the current 6042 service.
legacy_backup_dir="$(backup_legacy_database)"
if [ -n "$legacy_backup_dir" ]; then
    printf 'Backed up legacy database to %s/status.db.\n' "$legacy_backup_dir"
fi

install -d -o root -g root -m 0755 "$INSTALL_DIR" "$INSTALL_DIR/expo-launcher"
if [ "$project_dir" != "$INSTALL_DIR" ]; then
    install -o root -g root -m 0644 "$project_dir/expo_launcher.py" "$INSTALL_DIR/expo_launcher.py"
    for asset_name in index.html launcher.js networks.json styles.css; do
        install -o root -g root -m 0644 "$project_dir/expo-launcher/$asset_name" "$INSTALL_DIR/expo-launcher/$asset_name"
    done
fi
chown root:root "$INSTALL_DIR/expo_launcher.py" "$INSTALL_DIR/expo-launcher" "$INSTALL_DIR/expo-launcher"/*
chmod 0644 "$INSTALL_DIR/expo_launcher.py" "$INSTALL_DIR/expo-launcher"/*
chmod 0755 "$INSTALL_DIR/expo-launcher"

install -d -o root -g root -m 0750 "$ENV_DIR"
for instance in $instances; do
    install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0750 "$DATA_ROOT/$instance"
done
printf 'PORT=6043\n' > "$ENV_DIR/flat.env"
printf 'PORT=6044\n' > "$ENV_DIR/compartmentalized.env"
printf 'PORT=6045\n' > "$ENV_DIR/live.env"
chown root:root "$ENV_DIR"/*.env
chmod 0640 "$ENV_DIR"/*.env

render_unit "$project_dir/scripts/status-visualizer@.service.template" "/etc/systemd/system/status-visualizer@.service"
render_unit "$project_dir/scripts/status-visualizer-launcher.service.template" "/etc/systemd/system/status-visualizer-launcher.service"
systemctl daemon-reload

systemctl enable --now status-visualizer@flat.service
systemctl enable --now status-visualizer@compartmentalized.service
systemctl enable --now status-visualizer@live.service
wait_for_health "status-visualizer@flat.service" "http://127.0.0.1:6043/api/health"
wait_for_health "status-visualizer@compartmentalized.service" "http://127.0.0.1:6044/api/health"
wait_for_health "status-visualizer@live.service" "http://127.0.0.1:6045/api/health"
printf "All topology instances are healthy; switching port 6042 to the expo launcher.\n"

if systemctl cat status-visualizer.service >/dev/null 2>&1; then
    legacy_service_exists="true"
    if systemctl is-enabled --quiet status-visualizer.service; then
        legacy_was_enabled="true"
    fi
    if systemctl is-active --quiet status-visualizer.service; then
        legacy_was_active="true"
    fi
    cutover_started="true"
    systemctl stop status-visualizer.service
fi
systemctl enable --now status-visualizer-launcher.service
wait_for_health "status-visualizer-launcher.service" "http://127.0.0.1:6042/api/health"
if [ "$legacy_service_exists" = "true" ]; then
    systemctl disable status-visualizer.service
fi
cutover_started="false"
trap - ERR
retire_legacy_database

printf 'Expo launcher is healthy on port 6042.\n'
printf 'Restrict TCP ports 6042-6045 to trusted expo clients in the VM firewall.\n'
printf 'The live card remains hidden until enabled in %s/expo-launcher/networks.json.\n' "$INSTALL_DIR"
