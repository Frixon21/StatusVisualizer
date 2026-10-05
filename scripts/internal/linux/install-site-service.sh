#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_DIR="/opt/status-visualizer-mqtt-helper"
STATE_DIR="/var/lib/status-visualizer-mqtt-helper"
CONFIG_DIR="/etc/status-visualizer-mqtt-helper"
SERVICE_FILE="/etc/systemd/system/status-visualizer-mqtt-helper.service"
SERVICE_USER="${SUDO_USER:-}"

fail() { printf 'INSTALL FAILED: %s\n' "$*" >&2; exit 1; }

while [ "$#" -gt 0 ]; do
    case "$1" in
        --user) SERVICE_USER="${2:?--user requires a value}"; shift 2 ;;
        *) fail "Unknown option: $1" ;;
    esac
done

[ "$(id -u)" -eq 0 ] || fail "Run with sudo."
[ -n "$SERVICE_USER" ] && [ "$SERVICE_USER" != "root" ] ||
    fail "Run through sudo or pass --user USER so the service can read the export folder."
id -u "$SERVICE_USER" >/dev/null 2>&1 || fail "Linux user does not exist: $SERVICE_USER"
SERVICE_GROUP="$(id -gn "$SERVICE_USER")"

for command_name in python3 systemctl install cp id; do
    command -v "$command_name" >/dev/null 2>&1 || fail "Missing command: $command_name"
done
python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' ||
    fail "Python 3.11 or newer is required."
python3 -m venv --help >/dev/null 2>&1 || fail "Install the python3-venv package."

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
for path in "$SOURCE_DIR/mqtt_service.py" "$SOURCE_DIR/requirements.txt" "$SOURCE_DIR/config.json"; do
    [ -f "$path" ] || fail "Missing package file: $path"
done
[ -d "$SOURCE_DIR/lantopolog_mqtt_helper" ] || fail "Missing lantopolog_mqtt_helper package."
[ -d "$SOURCE_DIR/app" ] || fail "Missing app package."

systemctl stop status-visualizer-mqtt-helper.service 2>/dev/null || true
install -d -o root -g root -m 0755 "$INSTALL_DIR"
install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0750 "$STATE_DIR"
install -d -o root -g root -m 0700 "$CONFIG_DIR"
rm -rf -- "$INSTALL_DIR/lantopolog_mqtt_helper" "$INSTALL_DIR/app" "$INSTALL_DIR/.venv"
cp -a "$SOURCE_DIR/lantopolog_mqtt_helper" "$INSTALL_DIR/lantopolog_mqtt_helper"
cp -a "$SOURCE_DIR/app" "$INSTALL_DIR/app"
install -o root -g root -m 0644 "$SOURCE_DIR/mqtt_service.py" "$INSTALL_DIR/mqtt_service.py"
cp "$SOURCE_DIR/requirements.txt" "$INSTALL_DIR/requirements.txt"

python3 - "$SOURCE_DIR/config.json" "$STATE_DIR/config.json" "$STATE_DIR" <<'PY'
import json, pathlib, sys
source, target, state_dir = map(pathlib.Path, sys.argv[1:])
config = json.loads(source.read_text())
broker = config.get("broker")
if not isinstance(broker, dict) or not broker.get("host"):
    raise SystemExit("config.json broker.host is required")
if "password" in broker:
    raise SystemExit("config.json must not contain a password field")
export = pathlib.Path(config.get("export_folder", "")).expanduser()
if not export.is_dir():
    raise SystemExit(f"export_folder does not exist: {export}")
config["export_folder"] = str(export.resolve())
config["state_dir"] = str(state_dir)
target.write_text(json.dumps(config, indent=2) + "\n")
PY

python3 -m venv "$INSTALL_DIR/.venv"
PIP_DISABLE_PIP_VERSION_CHECK=1 "$INSTALL_DIR/.venv/bin/python" -m pip install \
    --no-cache-dir --requirement "$INSTALL_DIR/requirements.txt"

password=""
verified="false"
for attempt in 1 2 3; do
    printf "Enter MQTT broker password (input hidden): "
    IFS= read -r -s password
    printf '\nChecking MQTT password...\n'
    if STATUS_VISUALIZER_MQTT_PASSWORD="$password" \
        "$INSTALL_DIR/.venv/bin/python" - "$STATE_DIR/config.json" <<'PY'
import json, os, pathlib, sys, threading
import paho.mqtt.client as mqtt

config = json.loads(pathlib.Path(sys.argv[1]).read_text())
broker = config["broker"]
done = threading.Event()
result = {"ok": False, "message": "Timed out waiting for MQTT CONNACK"}
client = mqtt.Client(
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    client_id="statusvisualizer-linux-helper-install-check",
    protocol=mqtt.MQTTv311,
)
if broker.get("username"):
    client.username_pw_set(broker["username"], os.environ["STATUS_VISUALIZER_MQTT_PASSWORD"])
tls = broker.get("tls") or {}
if tls.get("enabled", True):
    client.tls_set(ca_certs=tls.get("ca_file"))

def connected(_client, _userdata, _flags, reason_code, _properties):
    failure = getattr(reason_code, "is_failure", None)
    if callable(failure):
        failure = failure()
    if failure is None:
        try:
            failure = int(reason_code) != 0
        except (TypeError, ValueError):
            failure = str(reason_code).casefold() not in {"0", "success", "success."}
    result["ok"] = not bool(failure)
    result["message"] = str(reason_code)
    done.set()

client.on_connect = connected
client.connect(broker["host"], int(broker.get("port", 8883)), int(broker.get("keepalive", 60)))
client.loop_start()
done.wait(10)
client.disconnect()
client.loop_stop()
if not result["ok"]:
    print(f"MQTT login rejected: {result['message']}", file=sys.stderr)
    raise SystemExit(1)
PY
    then
        verified="true"
        break
    fi
    printf 'Password check failed (%s/3).\n' "$attempt" >&2
done
[ "$verified" = "true" ] || fail "MQTT password was not accepted."
umask 077
printf '%s' "$password" > "$CONFIG_DIR/mqtt-password"
password=""

cat > "$INSTALL_DIR/start-service.sh" <<'SH'
#!/bin/sh
set -eu
export STATUS_VISUALIZER_MQTT_PASSWORD="$(cat "$CREDENTIALS_DIRECTORY/mqtt-password")"
exec /opt/status-visualizer-mqtt-helper/.venv/bin/python \
    /opt/status-visualizer-mqtt-helper/mqtt_service.py \
    --config /var/lib/status-visualizer-mqtt-helper/config.json
SH
chmod 0755 "$INSTALL_DIR/start-service.sh"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Status Visualizer LanTopoLog MQTT service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$INSTALL_DIR
LoadCredential=mqtt-password:$CONFIG_DIR/mqtt-password
ExecStart=$INSTALL_DIR/start-service.sh
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$STATE_DIR

[Install]
WantedBy=multi-user.target
EOF

chown -R root:root "$INSTALL_DIR"
chown -R "$SERVICE_USER:$SERVICE_GROUP" "$STATE_DIR"
systemctl daemon-reload
systemctl enable --now status-visualizer-mqtt-helper.service
sleep 2
systemctl is-active --quiet status-visualizer-mqtt-helper.service || {
    journalctl -u status-visualizer-mqtt-helper.service -n 40 --no-pager >&2 || true
    fail "Site service did not remain active."
}

printf '\nINSTALL SUCCEEDED\n'
watch_folder="$(python3 - "$STATE_DIR/config.json" <<'PY'
import json, pathlib, sys
print(json.loads(pathlib.Path(sys.argv[1]).read_text())["export_folder"])
PY
)"
printf 'Watching: %s\n' "$watch_folder"
printf 'Status: systemctl status status-visualizer-mqtt-helper\n'
