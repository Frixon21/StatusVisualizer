from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .config import load_config
from .service import run_helper


def resolve_helper_config_path(explicit: Path | None = None) -> Path:
    """Find helper config for double-click / scheduled-task use."""

    if explicit is not None:
        return explicit.expanduser().resolve()

    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "config.json")
    else:
        candidates.append(Path.cwd() / "config.json")

    program_data = os.getenv("PROGRAMDATA")
    if program_data:
        candidates.append(Path(program_data) / "StatusVisualizer" / "MqttHelper" / "config.json")

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    searched = ", ".join(str(path) for path in candidates) or "(none)"
    raise FileNotFoundError(
        "Helper configuration not found. Pass --config or place config.json next to the EXE. "
        f"Searched: {searched}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish LanTopoLog exports to Status Visualizer over MQTT.")
    parser.add_argument(
        "--config",
        required=False,
        type=Path,
        help="Path to the external JSON configuration file. Defaults to config.json next to the EXE.",
    )
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    arguments = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, arguments.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config(resolve_helper_config_path(arguments.config))
    try:
        run_helper(config)
    except KeyboardInterrupt:
        logging.getLogger("lantopolog-mqtt-helper").info("Helper stopped.")


if __name__ == "__main__":
    main()
