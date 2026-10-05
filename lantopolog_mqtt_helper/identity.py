from __future__ import annotations

import os
import uuid
from pathlib import Path


def atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(value, encoding="ascii")
    os.replace(temporary, path)


def load_or_create_client_id(path: Path) -> str:
    if path.exists():
        raw = path.read_text(encoding="ascii").strip()
        try:
            parsed = uuid.UUID(raw)
        except (ValueError, UnicodeError) as error:
            raise ValueError("The persisted MQTT client identity is invalid; restore it from backup.") from error
        if parsed.version != 4 or str(parsed) != raw.lower():
            raise ValueError("The persisted MQTT client identity is not a canonical UUID v4.")
        return str(parsed)
    client_id = str(uuid.uuid4())
    atomic_write_text(path, f"{client_id}\n")
    return client_id

