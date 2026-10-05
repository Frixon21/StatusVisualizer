from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PORT = 8092


def _project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_path(*parts: str) -> Path:
    bundle_root = Path(getattr(sys, "_MEIPASS", _project_root()))
    return bundle_root.joinpath(*parts)


def default_data_dir() -> Path:
    configured = os.getenv("STATUS_VISUALIZER_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    # Portable by default: keep the database next to the EXE / project root.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "data"
    return _project_root() / "data"


@dataclass(frozen=True, slots=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    data_dir: Path | None = None
    liveness_interval_seconds: float = 30.0
    liveness_timeout_seconds: float = 1.0
    liveness_max_concurrency: int = 12

    def __post_init__(self) -> None:
        if not 5 <= self.liveness_interval_seconds <= 3600:
            raise ValueError("Liveness interval must be between 5 and 3600 seconds")
        if not 0.1 <= self.liveness_timeout_seconds <= 2:
            raise ValueError("Liveness timeout must be between 0.1 and 2 seconds")
        if not 1 <= self.liveness_max_concurrency <= 32:
            raise ValueError("Liveness concurrency must be between 1 and 32")

    @property
    def database_path(self) -> Path:
        return Path(self.data_dir or default_data_dir()).resolve() / "status.db"
