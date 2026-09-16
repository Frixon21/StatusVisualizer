from __future__ import annotations

import argparse
import multiprocessing
from pathlib import Path

import uvicorn

from app.config import DEFAULT_PORT, Settings
from app.main import create_app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lantopolog topology visualizer")
    parser.add_argument("--host", default="127.0.0.1", help="Address to listen on")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="TCP port to listen on")
    parser.add_argument("--data-dir", type=Path, help="Directory containing the SQLite database")
    return parser.parse_args()


def main() -> None:
    multiprocessing.freeze_support()
    args = parse_args()
    settings = Settings(host=args.host, port=args.port, data_dir=args.data_dir)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
