#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_USER="status-visualizer"
SERVICE_GROUP="status-visualizer"
INSTALL_DIR="/opt/status-visualizer"
DATA_DIR="$INSTALL_DIR/data"
SERVICE_FILE="/etc/systemd/system/status-visualizer.service"
listen_host="127.0.0.1"
port="8092"
data_dir_explicit="false"

usage() {
    cat <<'EOF'
Usage: sudo bash scripts/install-linux.sh [options]

Options:
  --host ADDRESS       127.0.0.1 (default) or 0.0.0.0
  --port PORT          Listening port (default: 8092)
  --install-dir PATH   Application location (default: /opt/status-visualizer)
  --data-dir PATH      Database directory (default: <install-dir>/data)

The localhost default is intended for access through an SSH tunnel. Binding to
0.0.0.0 exposes the unauthenticated dashboard API to networks allowed by the VM firewall.
EOF
}

fail() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

trim_trailing_slashes() {
    local path_value="$1"
    while [ "${#path_value}" -gt 1 ] && [ "${path_value%/}" != "$path_value" ]; do
        path_value="${path_value%/}"
    done
    printf '%s\n' "$path_value"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --host)
            [ "$#" -ge 2 ] || fail "--host requires a value"
            listen_host="$2"
            shift 2
            ;;
        --port)
            [ "$#" -ge 2 ] || fail "--port requires a value"
            port="$2"
            shift 2
            ;;
        --install-dir)
            [ "$#" -ge 2 ] || fail "--install-dir requires a value"
            INSTALL_DIR="$2"
            shift 2
            ;;
        --data-dir)
            [ "$#" -ge 2 ] || fail "--data-dir requires a value"
            DATA_DIR="$2"
            data_dir_explicit="true"
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            fail "Unknown option: $1"
            ;;
    esac
done

if [ "$data_dir_explicit" != "true" ]; then
    DATA_DIR="$INSTALL_DIR/data"
fi

INSTALL_DIR="$(trim_trailing_slashes "$INSTALL_DIR")"
DATA_DIR="$(trim_trailing_slashes "$DATA_DIR")"

if [ "$(id -u)" -ne 0 ]; then
    fail "Run this installer as root, for example with sudo."
fi

validate_directory() {
    directory_name="$1"
    option_name="$2"
    case "$directory_name" in
        /*) ;;
        *) fail "$option_name must be an absolute path" ;;
    esac
    case "$directory_name" in
        *[!A-Za-z0-9._/-]*) fail "$option_name may contain only letters, numbers, dots, underscores, hyphens, and slashes" ;;
    esac
    case "/$directory_name/" in
        *'/../'*|*'/./'*) fail "$option_name must not contain . or .. path segments" ;;
    esac
    case "$directory_name" in
        /|/bin|/boot|/dev|/etc|/home|/lib|/lib64|/opt|/proc|/root|/run|/sbin|/srv|/sys|/tmp|/usr|/var)
            fail "$option_name is too broad"
            ;;
    esac
}

validate_directory "$INSTALL_DIR" "--install-dir"
validate_directory "$DATA_DIR" "--data-dir"
[ "$INSTALL_DIR" != "$DATA_DIR" ] || fail "--data-dir must be a subdirectory or a separate directory"
case "$DATA_DIR/" in
    "$INSTALL_DIR/app/"*|"$INSTALL_DIR/.venv/"*) fail "--data-dir must not be inside app or .venv" ;;
esac
case "$INSTALL_DIR/" in
    "$DATA_DIR/"*) fail "--install-dir must not be inside --data-dir" ;;
esac

case "$listen_host" in
    127.0.0.1|0.0.0.0) ;;
    *) fail "Host must be 127.0.0.1 or 0.0.0.0" ;;
esac
case "$port" in
    ''|*[!0-9]*) fail "Port must be an integer between 1 and 65535" ;;
esac
if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
    fail "Port must be an integer between 1 and 65535"
fi

required_commands="python3 systemctl ping install cp sed id useradd groupadd getent"
for command_name in $required_commands; do
    command -v "$command_name" >/dev/null 2>&1 || missing="${missing:-} $command_name"
done
if [ -n "${missing:-}" ]; then
    fail "Missing required commands:$missing. Install Python 3.11+, python3-venv, systemd, and iputils-ping."
fi
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
    || fail "Python 3.11 or newer is required"
python3 -m venv --help >/dev/null 2>&1 \
    || fail "Python venv support is missing; install the python3-venv package"

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
unit_template="$project_dir/scripts/status-visualizer.service.template"
for required_file in "$project_dir/run.py" "$project_dir/requirements.txt" "$unit_template"; do
    [ -f "$required_file" ] || fail "Required project file is missing: $required_file"
done
[ -d "$project_dir/app/static" ] || fail "Required application assets are missing"

if ! getent group "$SERVICE_GROUP" >/dev/null; then
    groupadd --system "$SERVICE_GROUP"
fi
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    nologin_shell="$(command -v nologin || true)"
    [ -n "$nologin_shell" ] || nologin_shell="/usr/sbin/nologin"
    useradd --system --gid "$SERVICE_GROUP" --home-dir "$DATA_DIR" \
        --no-create-home --shell "$nologin_shell" "$SERVICE_USER"
fi

install -d -o root -g root -m 0755 "$INSTALL_DIR" "$INSTALL_DIR/app"
install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0750 "$DATA_DIR"

if systemctl is-active --quiet status-visualizer.service; then
    systemctl stop status-visualizer.service
fi

rm -rf -- "$INSTALL_DIR/app"
install -d -o root -g root -m 0755 "$INSTALL_DIR/app"
cp -a "$project_dir/app/." "$INSTALL_DIR/app/"
install -o root -g root -m 0644 "$project_dir/run.py" "$INSTALL_DIR/run.py"
install -o root -g root -m 0644 "$project_dir/requirements.txt" "$INSTALL_DIR/requirements.txt"
python3 -m venv "$INSTALL_DIR/.venv"
PIP_DISABLE_PIP_VERSION_CHECK=1 "$INSTALL_DIR/.venv/bin/python" -m pip install \
    --no-cache-dir --requirement "$INSTALL_DIR/requirements.txt"
chown -R root:root "$INSTALL_DIR"
chmod -R u=rwX,go=rX "$INSTALL_DIR"
chown -R "$SERVICE_USER:$SERVICE_GROUP" "$DATA_DIR"
chmod -R u=rwX,g=rX,o= "$DATA_DIR"

unit_temp="$(mktemp)"
trap 'rm -f -- "$unit_temp"' EXIT
python3 - "$unit_template" "$unit_temp" "$INSTALL_DIR" "$DATA_DIR" "$listen_host" "$port" <<'PY'
from pathlib import Path
import sys

template_path, output_path, install_dir, data_dir, host, port = sys.argv[1:]
unit = Path(template_path).read_text(encoding="utf-8")
replacements = {
    "@@INSTALL_DIR@@": install_dir,
    "@@DATA_DIR@@": data_dir,
    "@@HOST@@": host,
    "@@PORT@@": port,
}
for marker, value in replacements.items():
    unit = unit.replace(marker, value)
Path(output_path).write_text(unit, encoding="utf-8")
PY
install -o root -g root -m 0644 "$unit_temp" "$SERVICE_FILE"

systemctl daemon-reload
systemctl enable --now status-visualizer.service

health_url="http://127.0.0.1:$port/api/health"
healthy="false"
attempt=1
while [ "$attempt" -le 40 ]; do
    if "$INSTALL_DIR/.venv/bin/python" -c \
        "import json, urllib.request; data=json.load(urllib.request.urlopen('$health_url', timeout=1)); raise SystemExit(0 if data.get('status') == 'ok' else 1)" \
        >/dev/null 2>&1; then
        healthy="true"
        break
    fi
    sleep 0.5
    attempt=$((attempt + 1))
done

if [ "$healthy" != "true" ]; then
    journalctl --unit status-visualizer.service --lines 50 --no-pager >&2 || true
    fail "Status Visualizer was installed but did not become healthy at $health_url"
fi

if [ "$listen_host" = "127.0.0.1" ]; then
    printf 'Status Visualizer is healthy. Connect securely with:\n'
    printf '  ssh -L %s:127.0.0.1:%s <user>@<vm-address>\n' "$port" "$port"
    printf 'Then open http://localhost:%s\n' "$port"
else
    printf 'Status Visualizer is healthy on port %s. Restrict that port to trusted clients in the VM firewall.\n' "$port"
fi
