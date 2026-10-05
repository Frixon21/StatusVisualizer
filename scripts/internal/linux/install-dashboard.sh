#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_DIR="/opt/status-visualizer"
DATA_DIR="/var/lib/status-visualizer"
CONFIG_DIR="/etc/status-visualizer"
SERVICE_FILE="/etc/systemd/system/status-visualizer.service"
SERVICE_USER="status-visualizer"
HOST="127.0.0.1"
PORT="8092"

fail() { printf 'INSTALL FAILED: %s\n' "$*" >&2; exit 1; }

while [ "$#" -gt 0 ]; do
    case "$1" in
        --host) HOST="${2:?--host requires a value}"; shift 2 ;;
        --port) PORT="${2:?--port requires a value}"; shift 2 ;;
        *) fail "Unknown option: $1" ;;
    esac
done

[ "$(id -u)" -eq 0 ] || fail "Run with sudo."
case "$HOST" in 127.0.0.1|0.0.0.0) ;; *) fail "--host must be 127.0.0.1 or 0.0.0.0" ;; esac
case "$PORT" in ''|*[!0-9]*) fail "--port must be 1-65535" ;; esac
[ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || fail "--port must be 1-65535"

for command_name in python3 systemctl install cp id useradd groupadd getent; do
    command -v "$command_name" >/dev/null 2>&1 || fail "Missing command: $command_name"
done
python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' ||
    fail "Python 3.11 or newer is required."
python3 -m venv --help >/dev/null 2>&1 || fail "Install the python3-venv package."

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
for path in "$SOURCE_DIR/run.py" "$SOURCE_DIR/requirements.txt" "$SOURCE_DIR/mqtt.json"; do
    [ -f "$path" ] || fail "Missing package file: $path"
done
[ -d "$SOURCE_DIR/app/static" ] || fail "Missing app/static."

if ! getent group "$SERVICE_USER" >/dev/null; then
    groupadd --system "$SERVICE_USER"
fi
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --gid "$SERVICE_USER" --home-dir "$DATA_DIR" \
        --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi

systemctl stop status-visualizer.service 2>/dev/null || true
install -d -o root -g root -m 0755 "$INSTALL_DIR"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$DATA_DIR"
install -d -o root -g root -m 0700 "$CONFIG_DIR"
rm -rf -- "$INSTALL_DIR/app" "$INSTALL_DIR/.venv"
cp -a "$SOURCE_DIR/app" "$INSTALL_DIR/app"
install -o root -g root -m 0644 "$SOURCE_DIR/run.py" "$INSTALL_DIR/run.py"
install -o root -g root -m 0644 "$SOURCE_DIR/requirements.txt" "$INSTALL_DIR/requirements.txt"
install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0640 "$SOURCE_DIR/mqtt.json" "$DATA_DIR/mqtt.json"

python3 - "$DATA_DIR/mqtt.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
config = json.loads(path.read_text())
if "password" in config:
    raise SystemExit("mqtt.json must not contain a password field")
if not config.get("host"):
    raise SystemExit("mqtt.json host is required")
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
        "$INSTALL_DIR/.venv/bin/python" - "$DATA_DIR/mqtt.json" <<'PY'
import json, os, pathlib, sys, threading
import paho.mqtt.client as mqtt

config = json.loads(pathlib.Path(sys.argv[1]).read_text())
done = threading.Event()
result = {"ok": False, "message": "Timed out waiting for MQTT CONNACK"}
client = mqtt.Client(
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    client_id="statusvisualizer-linux-install-check",
    protocol=mqtt.MQTTv311,
)
if config.get("username"):
    client.username_pw_set(config["username"], os.environ["STATUS_VISUALIZER_MQTT_PASSWORD"])
tls = config.get("tls") or {}
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
client.connect(config["host"], int(config.get("port", 8883)), int(config.get("keepalive", 60)))
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

cat > "$INSTALL_DIR/start-dashboard.sh" <<'SH'
#!/bin/sh
set -eu
export STATUS_VISUALIZER_MQTT_CONFIG="/var/lib/status-visualizer/mqtt.json"
export STATUS_VISUALIZER_MQTT_PASSWORD="$(cat "$CREDENTIALS_DIRECTORY/mqtt-password")"
exec /opt/status-visualizer/.venv/bin/python /opt/status-visualizer/run.py "$@"
SH
chmod 0755 "$INSTALL_DIR/start-dashboard.sh"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Status Visualizer dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
LoadCredential=mqtt-password:$CONFIG_DIR/mqtt-password
ExecStart=$INSTALL_DIR/start-dashboard.sh --host $HOST --port $PORT --data-dir $DATA_DIR
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$DATA_DIR

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now status-visualizer.service
healthy="false"
for _ in {1..40}; do
    if "$INSTALL_DIR/.venv/bin/python" -c \
        "import json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:$PORT/api/health',timeout=1)); raise SystemExit(d.get('status')!='ok')" \
        >/dev/null 2>&1; then
        healthy="true"
        break
    fi
    sleep 0.5
done
[ "$healthy" = "true" ] || {
    journalctl -u status-visualizer.service -n 40 --no-pager >&2 || true
    fail "Service did not become healthy."
}

printf '\nINSTALL SUCCEEDED\n'
printf 'Dashboard: http://%s:%s\n' "$HOST" "$PORT"
printf 'Config: %s/mqtt.json\n' "$DATA_DIR"
printf 'Status: systemctl status status-visualizer\n'
