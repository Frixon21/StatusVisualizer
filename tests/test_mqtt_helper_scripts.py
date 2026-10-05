from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (ROOT / "scripts" / name).read_text(encoding="utf-8")


def test_service_build_uses_its_own_pyinstaller_spec() -> None:
    script = _read("build-mqtt-service.ps1")
    assert "LanTopoLogMqttHelper.spec" in script
    assert "LanTopoLogMqttService.exe" in script
    assert "requirements-dev.txt" in script
    assert "dist-staging" in script


def test_service_installer_preserves_program_data_and_registers_startup_task() -> None:
    script = _read("internal/site-service-controller.ps1")
    assert '$env:ProgramData "StatusVisualizer\\MqttHelper"' in script
    assert "Register-ScheduledTask" in script
    assert "-AtStartup" in script
    assert "client-id" not in script
    assert "icacls.exe" in script
    assert "S-1-5-18" in script
    assert "Get-VerifiedMqttPassword" in script
    assert "Add-Type -AssemblyName System.Security" in script
    assert "ProtectedData" in script
    assert "mqtt-password.bin" in script


def test_service_uninstaller_keeps_identity_by_default() -> None:
    script = _read("internal/site-service-controller.ps1")
    assert "encrypted password were preserved" in script
    assert "ProgramData" in script


def test_mqtt_credentials_script_prompts_and_checks_broker() -> None:
    script = _read("internal/mqtt-credentials.ps1")
    assert "Read-Host -AsSecureString" in script
    assert "Test-MqttBrokerLogin" in script
    assert "Wrong MQTT username or password" in script
    assert "Get-VerifiedMqttPassword" in script


def test_pack_templates_contain_no_deployment_secrets() -> None:
    for name in (
        "dashboard-mqtt.json",
        "mqtt-service.config.json",
        "mqtt-service.config.linux.json",
    ):
        text = (ROOT / "pack-templates" / name).read_text(encoding="utf-8")
        assert "mqtt.example.com" in text
        assert '"password"' not in text
        assert "24.121" not in text


def test_deployment_pack_has_windows_and_linux_dashboard_and_service() -> None:
    script = _read("build-dist-status-visualizer.ps1")
    assert "Dashboard" in script
    assert "MqttService" in script
    assert "Dashboard-Linux" in script
    assert "MqttService-Linux" in script
    assert "dist-status-visualizer" in script
    assert "pack-templates" in script
    assert "pack-local" in script
    assert 'Join-Path $projectDir "mqtt.json"' not in script
    assert "Start Status Visualizer.bat" in script
    assert "INSTALL Service.bat" in script
    assert "UNINSTALL Service.bat" in script
    assert "site-service.ps1" in script
    assert "dashboard.ps1" in script
    assert "Stop-PackLockedProcesses" in script
    assert 'Copy-Item (Join-Path $projectDir "app") (Join-Path $linuxService "app") -Recurse' in script
    assert '"paho-mqtt>=2.1,<3`npydantic>=2.10,<3`n"' in script
    assert "24.121" not in script
    assert "REPLACE_ME" not in script


def test_linux_bundles_use_systemd_credentials() -> None:
    dashboard = _read("internal/linux/install-dashboard.sh")
    service = _read("internal/linux/install-site-service.sh")
    for script in (dashboard, service):
        assert "LoadCredential=mqtt-password:" in script
        assert "STATUS_VISUALIZER_MQTT_PASSWORD" in script
        assert "read -r -s password" in script
        assert "systemctl enable --now" in script
        assert '"password" in' in script
    assert "status-visualizer.service" in dashboard
    assert "status-visualizer-mqtt-helper.service" in service
    assert 'SERVICE_USER="${SUDO_USER:-}"' in service
    assert '[ -d "$SOURCE_DIR/app" ]' in service
    assert 'cp -a "$SOURCE_DIR/app" "$INSTALL_DIR/app"' in service
