from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

_EXPORT_SUFFIXES = {".csv", ".xml"}


def _paths(root: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            (path for path in root.rglob("*") if path.is_file() and path.suffix.casefold() in _EXPORT_SUFFIXES),
            key=lambda path: path.relative_to(root).as_posix().encode("utf-8"),
        )
    )


def _fingerprint(root: Path) -> tuple[tuple[str, int, int], ...]:
    return tuple(
        (path.relative_to(root).as_posix(), stat.st_size, stat.st_mtime_ns)
        for path in _paths(root)
        for stat in (path.stat(),)
    )


def collect_export_files(root: Path) -> dict[str, bytes]:
    before = _fingerprint(root)
    with tempfile.TemporaryDirectory(prefix="statusvisualizer-mqtt-") as temporary:
        staging = Path(temporary)
        for relative_name, _size, _mtime in before:
            source = root / Path(relative_name)
            destination = staging / Path(relative_name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        after = _fingerprint(root)
        if before != after:
            raise RuntimeError("The LanTopoLog export changed while it was being staged.")
        return {name: (staging / Path(name)).read_bytes() for name, _size, _mtime in before}


@dataclass(slots=True)
class ExportFolderWatcher:
    root: Path
    quiet_seconds: float = 10.0
    _last_fingerprint: tuple[tuple[str, int, int], ...] | None = None
    _changed_at: float | None = None
    _emitted_fingerprint: tuple[tuple[str, int, int], ...] | None = None

    def poll(self, *, now: float, force: bool = False) -> dict[str, bytes] | None:
        current = _fingerprint(self.root)
        if current != self._last_fingerprint:
            self._last_fingerprint = current
            self._changed_at = now
            return None
        if not current or self._changed_at is None or now - self._changed_at < self.quiet_seconds:
            return None
        if not force and self._emitted_fingerprint == current:
            return None
        try:
            files = collect_export_files(self.root)
        except (OSError, RuntimeError):
            self._last_fingerprint = None
            self._changed_at = now
            self._emitted_fingerprint = None
            return None
        self._emitted_fingerprint = current
        return files
