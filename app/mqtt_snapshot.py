from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import struct
import unicodedata
import uuid
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType

from app.lantopolog import MAX_EXPORT_BYTES, MAX_EXPORT_FILES, MAX_FILE_BYTES

MAX_SNAPSHOT_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024
SNAPSHOT_VERSION = 1
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class SnapshotValidationError(ValueError):
    """Raised when an MQTT snapshot is malformed or unsafe."""


@dataclass(frozen=True, slots=True)
class SnapshotPackage:
    files: Mapping[str, bytes]
    manifest: Mapping[str, object]
    snapshot_hash: str


def _client_uuid(value: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise SnapshotValidationError("The snapshot client ID is not a valid UUID.") from error
    if parsed.version != 4 or str(parsed) != str(value):
        raise SnapshotValidationError("The snapshot client ID must be a canonical UUID v4.")
    return str(parsed)


def _file_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise SnapshotValidationError("The snapshot contains an invalid file path.")
    if unicodedata.normalize("NFC", value) != value:
        raise SnapshotValidationError("The snapshot contains a non-canonical file path.")
    if value.startswith("/") or _DRIVE_RE.match(value):
        raise SnapshotValidationError("The snapshot contains an absolute file path.")
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts) or path.as_posix() != value:
        raise SnapshotValidationError("The snapshot contains a non-canonical file path.")
    if path.name.casefold() == "manifest.json":
        raise SnapshotValidationError("manifest.json is reserved for snapshot metadata.")
    if path.suffix.casefold() not in {".csv", ".xml"}:
        raise SnapshotValidationError("The snapshot contains an unsupported file type.")
    return value


def _normalized_files(files: Mapping[str, bytes]) -> dict[str, bytes]:
    if not isinstance(files, Mapping) or not files:
        raise SnapshotValidationError("The snapshot contains no export files.")
    if len(files) > MAX_EXPORT_FILES:
        raise SnapshotValidationError("The snapshot contains too many files.")
    normalized: dict[str, bytes] = {}
    folded: set[str] = set()
    total = 0
    for supplied_name, supplied_content in files.items():
        name = _file_path(supplied_name)
        key = name.casefold()
        if key in folded:
            raise SnapshotValidationError("The snapshot contains a duplicate file path.")
        if not isinstance(supplied_content, (bytes, bytearray)):
            raise SnapshotValidationError("Snapshot export files must contain bytes.")
        content = bytes(supplied_content)
        if len(content) > MAX_FILE_BYTES:
            raise SnapshotValidationError("A snapshot export file is too large.")
        total += len(content)
        if total > MAX_EXPORT_BYTES:
            raise SnapshotValidationError("The expanded snapshot is too large.")
        folded.add(key)
        normalized[name] = content
    return normalized


def calculate_snapshot_hash(files: Mapping[str, bytes]) -> str:
    """Hash canonical file names and contents, independent of ZIP metadata/order."""

    normalized = _normalized_files(files)
    digest = hashlib.sha256()
    for name in sorted(normalized, key=lambda item: item.encode("utf-8")):
        name_bytes = name.encode("utf-8")
        content = normalized[name]
        digest.update(struct.pack(">Q", len(name_bytes)))
        digest.update(name_bytes)
        digest.update(struct.pack(">Q", len(content)))
        digest.update(content)
    return digest.hexdigest()


def build_snapshot_zip(files: Mapping[str, bytes], client_id: str) -> bytes:
    normalized = _normalized_files(files)
    canonical_id = _client_uuid(client_id)
    snapshot_hash = calculate_snapshot_hash(normalized)
    manifest = json.dumps(
        {"version": SNAPSHOT_VERSION, "client_id": canonical_id, "snapshot_hash": snapshot_hash},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in sorted(normalized, key=lambda item: item.encode("utf-8")):
            archive.writestr(name, normalized[name])
        archive.writestr("manifest.json", manifest)
    payload = output.getvalue()
    if len(payload) > MAX_SNAPSHOT_BYTES:
        raise SnapshotValidationError("The compressed snapshot is too large.")
    return payload


def _read_entry(archive: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    if info.file_size > limit:
        raise SnapshotValidationError("A snapshot entry is too large.")
    with archive.open(info, "r") as source:
        content = source.read(limit + 1)
    if len(content) > limit:
        raise SnapshotValidationError("A snapshot entry is too large.")
    return content


def read_snapshot_zip(payload: bytes, expected_client_id: str) -> SnapshotPackage:
    canonical_id = _client_uuid(expected_client_id)
    if not isinstance(payload, (bytes, bytearray)) or len(payload) > MAX_SNAPSHOT_BYTES:
        raise SnapshotValidationError("The compressed snapshot is too large.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(bytes(payload)), "r")
    except (zipfile.BadZipFile, OSError) as error:
        raise SnapshotValidationError("The snapshot is not a valid ZIP archive.") from error
    try:
        infos = archive.infolist()
        if not infos or len(infos) > MAX_EXPORT_FILES + 1:
            raise SnapshotValidationError("The snapshot contains an invalid number of files.")
        seen: set[str] = set()
        manifest_info: zipfile.ZipInfo | None = None
        files: dict[str, bytes] = {}
        expanded = 0
        for info in infos:
            name = info.filename
            folded = name.casefold()
            if folded in seen:
                raise SnapshotValidationError("The snapshot contains a duplicate file path.")
            seen.add(folded)
            if info.is_dir():
                raise SnapshotValidationError("The snapshot contains an unsupported directory entry.")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise SnapshotValidationError("The snapshot contains a symlink.")
            if info.flag_bits & 0x1:
                raise SnapshotValidationError("The snapshot contains an encrypted entry.")
            if folded == "manifest.json":
                if name != "manifest.json":
                    raise SnapshotValidationError("The manifest path is ambiguous.")
                manifest_info = info
                continue
            canonical_name = _file_path(name)
            content = _read_entry(archive, info, MAX_FILE_BYTES)
            expanded += len(content)
            if expanded > MAX_EXPORT_BYTES:
                raise SnapshotValidationError("The expanded snapshot is too large.")
            files[canonical_name] = content
        if manifest_info is None:
            raise SnapshotValidationError("The snapshot manifest is missing.")
        raw_manifest = _read_entry(archive, manifest_info, MAX_MANIFEST_BYTES)
    except (RuntimeError, NotImplementedError, zipfile.BadZipFile, OSError) as error:
        raise SnapshotValidationError("The snapshot ZIP cannot be read safely.") from error
    finally:
        archive.close()
    try:
        manifest = json.loads(raw_manifest.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SnapshotValidationError("The snapshot manifest is invalid.") from error
    required = {"version", "client_id", "snapshot_hash"}
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise SnapshotValidationError("The snapshot manifest has unsupported fields.")
    if type(manifest["version"]) is not int or manifest["version"] != SNAPSHOT_VERSION:
        raise SnapshotValidationError("The snapshot manifest version is unsupported.")
    if manifest["client_id"] != canonical_id:
        raise SnapshotValidationError("The snapshot manifest client ID does not match the topic.")
    claimed_hash = manifest["snapshot_hash"]
    if not isinstance(claimed_hash, str) or not _HASH_RE.fullmatch(claimed_hash):
        raise SnapshotValidationError("The snapshot manifest hash is invalid.")
    actual_hash = calculate_snapshot_hash(files)
    if claimed_hash != actual_hash:
        raise SnapshotValidationError("The snapshot hash does not match its contents.")
    return SnapshotPackage(
        files=MappingProxyType(dict(files)),
        manifest=MappingProxyType(dict(manifest)),
        snapshot_hash=actual_hash,
    )
