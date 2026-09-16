from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (PROJECT_ROOT / path).read_text(encoding="utf-8")


def test_instance_unit_is_isolated_hardened_and_ping_capable() -> None:
    unit = _read("scripts/status-visualizer@.service.template")

    assert "EnvironmentFile=@@ENV_DIR@@/%i.env" in unit
    assert "--data-dir @@DATA_ROOT@@/%i" in unit
    assert "ReadWritePaths=@@DATA_ROOT@@/%i" in unit
    assert "ProtectSystem=strict" in unit
    assert "NoNewPrivileges=true" in unit
    assert "CapabilityBoundingSet=CAP_NET_RAW" in unit
    assert "AmbientCapabilities=CAP_NET_RAW" in unit


def test_launcher_unit_is_hardened_without_ping_or_write_access() -> None:
    unit = _read("scripts/status-visualizer-launcher.service.template")

    assert "User=status-visualizer" in unit
    assert "Group=status-visualizer" in unit
    assert "ProtectSystem=strict" in unit
    assert "NoNewPrivileges=true" in unit
    assert "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX" in unit
    assert "--port 6042" in unit
    assert "expo_launcher.py" in unit
    assert "CAP_NET_RAW" not in unit
    assert "CapabilityBoundingSet=" in unit
    assert "AmbientCapabilities=" in unit
    assert "ReadWritePaths=" not in unit


def test_expo_installer_defines_instances_ports_and_health_gated_cutover() -> None:
    installer = _read("scripts/install-expo-linux.sh")

    for name, port in (("flat", "6043"), ("compartmentalized", "6044"), ("live", "6045")):
        assert name in installer
        assert port in installer
        assert f"status-visualizer@{name}.service" in installer
        assert f"http://127.0.0.1:{port}/api/health" in installer

    instances_ready = installer.index('printf "All topology instances are healthy')
    legacy_stop = installer.index("systemctl stop status-visualizer.service")
    launcher_start = installer.index("systemctl enable --now status-visualizer-launcher.service")
    launcher_health = installer.index("http://127.0.0.1:6042/api/health")
    legacy_disable = installer.index("systemctl disable status-visualizer.service")
    assert instances_ready < legacy_stop < launcher_start < launcher_health < legacy_disable
    assert "rollback_cutover" in installer
    assert 'legacy_was_active="false"' in installer


def test_expo_installer_uses_consistent_non_destructive_database_backup() -> None:
    installer = _read("scripts/install-expo-linux.sh")

    assert 'BACKUP_ROOT="$INSTALL_DIR/backups"' in installer
    assert 'LEGACY_DATABASE="$INSTALL_DIR/data/status.db"' in installer
    assert 'datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")' in installer
    assert ".backup(" in installer
    assert "sha256" in installer
    assert 'rm -rf -- "$LEGACY_DATABASE"' not in installer
    assert "retire_legacy_database" in installer
    assert 'mv -- "$LEGACY_DATABASE"' in installer
    assert 'chown root:root "$INSTALL_DIR/expo_launcher.py"' in installer
    retire_call = installer.rindex("\nretire_legacy_database\n")
    assert installer.index("wait_for_health \"status-visualizer-launcher.service\"") < retire_call
    assert installer.index("backup_legacy_database") < installer.index(
        "systemctl enable --now status-visualizer@flat.service"
    )


def test_expo_installer_installs_known_launcher_assets_without_recursive_copy() -> None:
    installer = _read("scripts/install-expo-linux.sh")

    assert "for asset_name in index.html launcher.js networks.json styles.css" in installer
    assert 'install -o root -g root -m 0644 "$project_dir/expo-launcher/$asset_name"' in installer
    assert 'cp -a "$project_dir/expo-launcher/."' not in installer


def test_expo_uninstaller_preserves_data_without_explicit_purge() -> None:
    uninstaller = _read("scripts/uninstall-expo-linux.sh")

    assert "--purge-data" in uninstaller
    assert 'if [ "$purge_data" = "true" ]' in uninstaller
    assert 'if [ "$DATA_ROOT" != "/srv/StatusVisualizer/data" ]' in uninstaller
    assert '[ -L "$DATA_ROOT" ]' in uninstaller
    assert 'rm -rf -- "$DATA_ROOT"' in uninstaller
    assert "Expo topology data was kept" in uninstaller


def test_expo_runbook_covers_operations_security_and_live_activation() -> None:
    runbook = _read("EXPO-RUNBOOK.md")

    for required in (
        "status-visualizer@flat",
        "status-visualizer@compartmentalized",
        "status-visualizer@live",
        "status-visualizer-launcher",
        "journalctl",
        "/srv/StatusVisualizer/backups",
        "6042–6045",
        "trusted",
        "offline",
        "Lantopolog",
        '"port": "6042"',
    ):
        assert required in runbook
    assert runbook.index("6045/api/health") < runbook.index('"visible": true')


def test_linux_builder_packages_all_expo_deployment_assets() -> None:
    builder = _read("scripts/build-linux-folder.ps1")

    for required in (
        "expo_launcher.py",
        "expo-launcher",
        "install-expo-linux.sh",
        "uninstall-expo-linux.sh",
        "status-visualizer@.service.template",
        "status-visualizer-launcher.service.template",
        "EXPO-RUNBOOK.md",
    ):
        assert required in builder
