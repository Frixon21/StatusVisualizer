from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read(name: str) -> str:
    return (PROJECT_ROOT / name).read_text(encoding="utf-8")


def test_docker_image_runs_the_web_app_as_a_non_root_user() -> None:
    dockerfile = _read("Dockerfile")

    assert "FROM python:3.12-slim" in dockerfile
    assert "iputils-ping" in dockerfile
    assert "USER status-visualizer" in dockerfile
    assert 'CMD ["python", "run.py", "--host", "0.0.0.0"' in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "/api/health" in dockerfile


def test_compose_keeps_sqlite_data_and_limits_host_access() -> None:
    compose = _read("compose.yaml")

    assert '"${STATUS_VISUALIZER_BIND:-127.0.0.1}:${STATUS_VISUALIZER_PORT:-8092}:8092"' in compose
    assert "status-visualizer-data:/data" in compose
    assert "cap_drop:" in compose and "- ALL" in compose
    assert "cap_add:" in compose and "- NET_RAW" in compose
    assert "no-new-privileges:true" in compose


def test_docker_context_excludes_local_state_and_build_outputs() -> None:
    dockerignore = _read(".dockerignore")

    for excluded in ("data/", "dist/", "build/", ".venv/", ".env", "*.db"):
        assert excluded in dockerignore


def test_runtime_requirements_pin_direct_framework_dependencies() -> None:
    requirements = _read("requirements.txt")

    assert "fastapi" in requirements
    assert "uvicorn" in requirements
    assert "pydantic>=2" in requirements


def test_readme_documents_the_complete_compose_workflow() -> None:
    readme = _read("README.md")

    assert "## Run with Docker" in readme
    assert "docker compose up --build -d" in readme
    assert "docker compose logs -f" in readme
    assert "docker compose down" in readme
    assert "docker compose down -v" in readme
    assert "STATUS_VISUALIZER_PORT" in readme
    assert "Docker Desktop's Linux VM" in readme


def test_repeatable_docker_smoke_test_covers_health_and_persistence() -> None:
    script = _read("scripts/docker-smoke.ps1")

    assert "docker compose" in script
    assert "/api/health" in script
    assert "/api/devices" in script
    assert "Persistence check failed" in script
    assert "down --volumes --remove-orphans" in script
