#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_DIR="/opt/status-visualizer"
DATA_DIR="$INSTALL_DIR/data"
SERVICE_FILE="/etc/systemd/system/status-visualizer.service"
purge_data="false"
data_dir_explicit="false"

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
        --purge-data) purge_data="true" ;;
        --install-dir)
            [ "$#" -ge 2 ] || fail "--install-dir requires a value"
            INSTALL_DIR="$2"
            shift
            ;;
        --data-dir)
            [ "$#" -ge 2 ] || fail "--data-dir requires a value"
            DATA_DIR="$2"
            data_dir_explicit="true"
            shift
            ;;
        --help|-h)
            printf 'Usage: sudo bash scripts/uninstall-linux.sh [--install-dir PATH] [--data-dir PATH] [--purge-data]\n'
            exit 0
            ;;
        *) fail "Unknown option: $1" ;;
    esac
    shift
done


if [ "$data_dir_explicit" != "true" ]; then
    DATA_DIR="$INSTALL_DIR/data"
fi

INSTALL_DIR="$(trim_trailing_slashes "$INSTALL_DIR")"
DATA_DIR="$(trim_trailing_slashes "$DATA_DIR")"

if [ "$(id -u)" -ne 0 ]; then
    fail "Run this uninstaller as root, for example with sudo."
fi

validate_directory() {
    directory_name="$1"
    option_name="$2"
    case "$directory_name" in
        /*) ;;
        *) fail "$option_name must be an absolute path" ;;
    esac
    case "$directory_name" in
        *[!A-Za-z0-9._/-]*) fail "$option_name contains unsupported characters" ;;
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

systemctl disable --now status-visualizer.service >/dev/null 2>&1 || true
rm -f -- "$SERVICE_FILE"
systemctl daemon-reload
systemctl reset-failed status-visualizer.service >/dev/null 2>&1 || true

rm -rf -- "$INSTALL_DIR/app" "$INSTALL_DIR/.venv"
rm -f -- "$INSTALL_DIR/run.py" "$INSTALL_DIR/requirements.txt"

if [ "$purge_data" = "true" ]; then
    rm -rf -- "$DATA_DIR"
    rmdir -- "$INSTALL_DIR" >/dev/null 2>&1 || true
    userdel status-visualizer >/dev/null 2>&1 || true
    groupdel status-visualizer >/dev/null 2>&1 || true
    printf 'Application and topology data were removed.\n'
else
    rmdir -- "$INSTALL_DIR" >/dev/null 2>&1 || true
    printf 'Application removed. Topology data was kept at %s\n' "$DATA_DIR"
fi
