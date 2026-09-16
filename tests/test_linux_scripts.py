from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (PROJECT_ROOT / path).read_text(encoding="utf-8")


def test_linux_installer_validates_root_prerequisites_and_network_options() -> None:
    installer = _read("scripts/install-linux.sh")

    assert 'if [ "$(id -u)" -ne 0 ]' in installer
    assert "python3 systemctl ping" in installer
    assert "--host" in installer and "--port" in installer
    assert "127.0.0.1|0.0.0.0" in installer
    assert "Port must be an integer between 1 and 65535" in installer


def test_linux_installer_uses_a_dedicated_user_and_persistent_data_directory() -> None:
    installer = _read("scripts/install-linux.sh")

    assert 'SERVICE_USER="status-visualizer"' in installer
    assert 'INSTALL_DIR="/opt/status-visualizer"' in installer
    assert 'DATA_DIR="$INSTALL_DIR/data"' in installer
    assert "--install-dir" in installer
    assert "--data-dir" in installer
    assert "useradd --system" in installer
    assert 'python3 -m venv "$INSTALL_DIR/.venv"' in installer
    assert 'chmod -R u=rwX,g=rX,o= "$DATA_DIR"' in installer
    assert "requirements.txt" in installer


def test_linux_scripts_normalize_custom_paths_before_destructive_operations() -> None:
    installer = _read("scripts/install-linux.sh")
    uninstaller = _read("scripts/uninstall-linux.sh")

    for script in (installer, uninstaller):
        assert "trim_trailing_slashes()" in script
        assert 'INSTALL_DIR="$(trim_trailing_slashes "$INSTALL_DIR")"' in script
        assert 'DATA_DIR="$(trim_trailing_slashes "$DATA_DIR")"' in script
        assert script.index('INSTALL_DIR="$(trim_trailing_slashes "$INSTALL_DIR")"') < script.index(
            'validate_directory "$INSTALL_DIR" "--install-dir"'
        )
        assert script.index('DATA_DIR="$(trim_trailing_slashes "$DATA_DIR")"') < script.index(
            'validate_directory "$DATA_DIR" "--data-dir"'
        )
        assert "/|/bin|/boot|/dev|/etc|/home|/lib|/lib64|/opt|/proc|/root|/run|/sbin|/srv|/sys|/tmp|/usr|/var" in script


def test_systemd_service_is_hardened_but_can_write_data_and_ping() -> None:
    unit = _read("scripts/status-visualizer.service.template")

    assert "User=status-visualizer" in unit
    assert "Group=status-visualizer" in unit
    assert "ProtectSystem=strict" in unit
    assert "ProtectHome=true" in unit
    assert "NoNewPrivileges=true" in unit
    assert "WorkingDirectory=@@INSTALL_DIR@@" in unit
    assert "ExecStart=@@INSTALL_DIR@@/.venv/bin/python" in unit
    assert "--data-dir @@DATA_DIR@@" in unit
    assert "ReadWritePaths=@@DATA_DIR@@" in unit
    assert "CapabilityBoundingSet=CAP_NET_RAW" in unit
    assert "AmbientCapabilities=CAP_NET_RAW" in unit
    assert "@@HOST@@" in unit and "@@PORT@@" in unit


def test_linux_installer_enables_service_and_requires_a_healthy_start() -> None:
    installer = _read("scripts/install-linux.sh")

    assert "systemctl daemon-reload" in installer
    assert "systemctl enable --now status-visualizer.service" in installer
    assert "/api/health" in installer
    assert "did not become healthy" in installer
    assert "journalctl --unit status-visualizer.service" in installer


def test_linux_uninstaller_preserves_data_unless_purge_is_explicit() -> None:
    uninstaller = _read("scripts/uninstall-linux.sh")

    assert "--purge-data" in uninstaller
    assert "--install-dir" in uninstaller
    assert "--data-dir" in uninstaller
    assert 'if [ "$purge_data" = "true" ]' in uninstaller
    assert 'rm -rf -- "$DATA_DIR"' in uninstaller
    assert 'rm -rf -- "$INSTALL_DIR/app" "$INSTALL_DIR/.venv"' in uninstaller
    assert "Topology data was kept" in uninstaller


def test_readme_documents_native_linux_operation_and_remote_access_risk() -> None:
    readme = _read("README.md")

    assert "## Run on a Linux VM without Docker" in readme
    assert "install-linux.sh --host 0.0.0.0" in readme
    assert "journalctl -u status-visualizer" in readme
    assert "ssh -L 8092:127.0.0.1:8092" in readme
    assert "does not have authentication" in readme
    assert "uninstall-linux.sh --purge-data" in readme
    assert "/opt/status-visualizer/data/status.db" in readme
    assert "--install-dir /srv/status-visualizer" in readme


def test_linux_folder_builder_copies_only_the_runtime_package() -> None:
    builder = _read("scripts/build-linux-folder.ps1")

    for required_path in (
        "app",
        "run.py",
        "requirements.txt",
        "scripts/install-linux.sh",
        "scripts/uninstall-linux.sh",
        "scripts/status-visualizer.service.template",
        "LINUX-README.md",
    ):
        assert required_path in builder

    for excluded_path in ("data", ".venv", "*.exe", "build", "dist", "tests"):
        assert excluded_path in builder
    assert "OutputPath must be a child of the project root" in builder


def test_linux_package_has_copy_and_run_instructions() -> None:
    instructions = _read("LINUX-README.md")

    assert "Copy this entire folder" in instructions
    assert "sudo bash scripts/install-linux.sh" in instructions
    assert "http://localhost:8092" in instructions
    assert "systemctl status status-visualizer" in instructions
    assert "uninstall-linux.sh" in instructions
    assert "--install-dir /srv/status-visualizer" in instructions
    assert "--data-dir /srv/status-visualizer/data" in instructions
    assert "/opt/status-visualizer/data/status.db" in instructions


def test_generated_linux_folder_is_complete_and_contains_no_local_state() -> None:
    package = PROJECT_ROOT / "status-visualizer-linux"
    required_files = {
        "LINUX-README.md",
        "requirements.txt",
        "run.py",
        "app/__init__.py",
        "app/config.py",
        "app/database.py",
        "app/main.py",
        "app/static/index.html",
        "app/static/app.js",
        "app/static/styles.css",
        "scripts/install-linux.sh",
        "scripts/uninstall-linux.sh",
        "scripts/status-visualizer.service.template",
        "expo_launcher.py",
        "expo-launcher/index.html",
        "expo-launcher/launcher.js",
        "expo-launcher/networks.json",
        "expo-launcher/styles.css",
        "EXPO-RUNBOOK.md",
        "scripts/install-expo-linux.sh",
        "scripts/uninstall-expo-linux.sh",
        "scripts/status-visualizer@.service.template",
        "scripts/status-visualizer-launcher.service.template",
    }
    packaged_files = {
        path.relative_to(package).as_posix()
        for path in package.rglob("*")
        if path.is_file()
    }

    assert required_files <= packaged_files
    assert not any(
        "__pycache__" in path
        or path.endswith((".pyc", ".exe", ".db"))
        or path.startswith(("data/", ".venv/", "build/", "dist/", "tests/"))
        for path in packaged_files
    )
