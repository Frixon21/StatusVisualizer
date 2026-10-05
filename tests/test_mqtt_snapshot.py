from __future__ import annotations

import io
import json
import struct
import uuid
import zipfile

import pytest

from app import mqtt_snapshot
from app.mqtt_snapshot import (
    MAX_SNAPSHOT_BYTES,
    SnapshotValidationError,
    build_snapshot_zip,
    calculate_snapshot_hash,
    read_snapshot_zip,
)
from tests.lantopolog_fixture import export_files


def _bytes_files() -> dict[str, bytes]:
    return {name: content.encode("utf-8") for name, content in export_files().items()}


def test_hash_is_deterministic_and_covers_names_and_contents() -> None:
    files = _bytes_files()
    reverse = dict(reversed(tuple(files.items())))
    expected = calculate_snapshot_hash(files)

    assert calculate_snapshot_hash(reverse) == expected
    assert calculate_snapshot_hash({**files, "extra.csv": b"x"}) != expected
    assert calculate_snapshot_hash({**files, "sw_list.csv": b"different"}) != expected


def test_hash_uses_unambiguous_length_prefixes() -> None:
    import hashlib

    digest = hashlib.sha256()
    digest.update(struct.pack(">Q", len(b"a.csv")))
    digest.update(b"a.csv")
    digest.update(struct.pack(">Q", 3))
    digest.update(b"abc")

    assert calculate_snapshot_hash({"a.csv": b"abc"}) == digest.hexdigest()


def test_zip_round_trip_has_versioned_manifest_and_original_files() -> None:
    client_id = str(uuid.uuid4())
    files = _bytes_files()
    payload = build_snapshot_zip(files, client_id)
    package = read_snapshot_zip(payload, client_id)

    assert package.files == files
    assert package.snapshot_hash == calculate_snapshot_hash(files)
    assert package.manifest == {
        "version": 1,
        "client_id": client_id,
        "snapshot_hash": package.snapshot_hash,
    }


@pytest.mark.parametrize(
    "bad_name",
    ("../sw_list.csv", "/sw_list.csv", "C:/sw_list.csv", "folder\\sw_list.csv"),
)
def test_build_rejects_unsafe_or_noncanonical_paths(bad_name: str) -> None:
    with pytest.raises(SnapshotValidationError):
        build_snapshot_zip({bad_name: b"x"}, str(uuid.uuid4()))


def _custom_zip(entries: list[tuple[zipfile.ZipInfo | str, bytes]]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return output.getvalue()


def _manifest(client_id: str, files: dict[str, bytes], **changes: object) -> bytes:
    value = {
        "version": 1,
        "client_id": client_id,
        "snapshot_hash": calculate_snapshot_hash(files),
        **changes,
    }
    return json.dumps(value).encode()


def test_reader_rejects_wrong_client_hash_and_version() -> None:
    client_id = str(uuid.uuid4())
    files = {"sw_list.csv": b"IP;Name\n192.0.2.1;switch\n"}
    for changes, expected in (
        ({"client_id": str(uuid.uuid4())}, "client"),
        ({"snapshot_hash": "0" * 64}, "hash"),
        ({"version": 2}, "version"),
    ):
        manifest = json.loads(_manifest(client_id, files))
        manifest.update(changes)
        payload = _custom_zip([
            ("sw_list.csv", files["sw_list.csv"]),
            ("manifest.json", json.dumps(manifest).encode()),
        ])
        with pytest.raises(SnapshotValidationError, match=expected):
            read_snapshot_zip(payload, client_id)


def test_reader_rejects_duplicates_symlinks_encryption_and_oversize() -> None:
    client_id = str(uuid.uuid4())
    files = {"sw_list.csv": b"x"}
    manifest = _manifest(client_id, files)
    duplicate = _custom_zip(
        [("sw_list.csv", b"x"), ("SW_LIST.CSV", b"x"), ("manifest.json", manifest)]
    )
    with pytest.raises(SnapshotValidationError, match="duplicate"):
        read_snapshot_zip(duplicate, client_id)

    symlink = zipfile.ZipInfo("sw_list.csv")
    symlink.create_system = 3
    symlink.external_attr = 0o120777 << 16
    payload = _custom_zip([(symlink, b"x"), ("manifest.json", manifest)])
    with pytest.raises(SnapshotValidationError, match="symlink"):
        read_snapshot_zip(payload, client_id)

    raw = bytearray(_custom_zip([("sw_list.csv", b"x"), ("manifest.json", manifest)]))
    local = raw.index(b"PK\x03\x04")
    central = raw.index(b"PK\x01\x02")
    raw[local + 6] |= 0x1
    raw[central + 8] |= 0x1
    payload = bytes(raw)
    with pytest.raises(SnapshotValidationError, match="encrypted"):
        read_snapshot_zip(payload, client_id)

    with pytest.raises(SnapshotValidationError, match="large"):
        read_snapshot_zip(b"x" * (MAX_SNAPSHOT_BYTES + 1), client_id)


@pytest.mark.parametrize(
    ("payload_builder", "match"),
    (
        (lambda _cid: b"not-a-zip", "valid ZIP"),
        (
            lambda cid: _custom_zip([
                (zipfile.ZipInfo("subdir/"), b""),
                ("manifest.json", json.dumps({
                    "version": 1,
                    "client_id": cid,
                    "snapshot_hash": "0" * 64,
                }).encode()),
            ]),
            "directory",
        ),
        (
            lambda cid: _custom_zip([
                ("../sw_list.csv", b"x"),
                ("manifest.json", json.dumps({
                    "version": 1,
                    "client_id": cid,
                    "snapshot_hash": "0" * 64,
                }).encode()),
            ]),
            "path",
        ),
        (
            lambda _cid: _custom_zip([
                ("Manifest.json", b"{}"),
                ("sw_list.csv", b"x"),
            ]),
            "ambiguous",
        ),
        (
            lambda cid: _custom_zip([
                ("notes.txt", b"x"),
                ("manifest.json", json.dumps({
                    "version": 1,
                    "client_id": cid,
                    "snapshot_hash": "0" * 64,
                }).encode()),
            ]),
            "unsupported",
        ),
        (
            lambda cid: _custom_zip([
                ("sw_list.csv", b"x"),
                ("manifest.json", json.dumps({
                    "version": 1,
                    "client_id": cid,
                    "snapshot_hash": calculate_snapshot_hash({"sw_list.csv": b"x"}),
                    "extra": True,
                }).encode()),
            ]),
            "unsupported fields",
        ),
        (
            lambda cid: _custom_zip([
                ("manifest.json", json.dumps({
                    "version": 1,
                    "client_id": cid,
                    "snapshot_hash": "0" * 64,
                }).encode()),
            ]),
            "no export files",
        ),
    ),
)
def test_reader_rejects_malicious_or_malformed_archives(payload_builder, match: str) -> None:
    client_id = str(uuid.uuid4())
    with pytest.raises(SnapshotValidationError, match=match):
        read_snapshot_zip(payload_builder(client_id), client_id)


def test_reader_rejects_oversized_manifest_and_file_count() -> None:
    from app.mqtt_snapshot import MAX_EXPORT_FILES, MAX_MANIFEST_BYTES

    client_id = str(uuid.uuid4())
    huge_manifest = b"{" + (b"x" * (MAX_MANIFEST_BYTES + 8)) + b"}"
    with pytest.raises(SnapshotValidationError, match="too large"):
        read_snapshot_zip(_custom_zip([("sw_list.csv", b"x"), ("manifest.json", huge_manifest)]), client_id)

    entries = [(f"file-{index}.csv", b"x") for index in range(MAX_EXPORT_FILES + 1)]
    entries.append(("manifest.json", b"{}"))
    with pytest.raises(SnapshotValidationError, match="invalid number of files"):
        read_snapshot_zip(_custom_zip(entries), client_id)


def test_hash_excludes_manifest_and_zip_metadata() -> None:
    client_id = str(uuid.uuid4())
    files = {"a.csv": b"one", "b.csv": b"two"}
    expected = calculate_snapshot_hash(files)
    first = build_snapshot_zip(files, client_id)
    second = build_snapshot_zip({"b.csv": b"two", "a.csv": b"one"}, client_id)
    assert read_snapshot_zip(first, client_id).snapshot_hash == expected
    assert read_snapshot_zip(second, client_id).snapshot_hash == expected
    assert expected != calculate_snapshot_hash({**files, "c.csv": b"x"})


@pytest.mark.parametrize(
    "files",
    [
        {},
        {"manifest.json": b"x"},
        {"notes.txt": b"x"},
        {"a.csv": "not-bytes"},
        {"A.csv": b"one", "a.csv": b"two"},
        {"a//b.csv": b"x"},
        {"caf\u0065\u0301.csv": b"x"},
        {"nul\x00.csv": b"x"},
    ],
)
def test_build_rejects_invalid_file_collections(files) -> None:
    with pytest.raises(SnapshotValidationError):
        build_snapshot_zip(files, str(uuid.uuid4()))


@pytest.mark.parametrize(
    "client_id",
    [
        "not-a-uuid",
        "12345678-1234-1234-1234-123456789abc",
        "550E8400-E29B-41D4-A716-446655440000",
    ],
)
def test_build_rejects_noncanonical_client_ids(client_id: str) -> None:
    with pytest.raises(SnapshotValidationError, match="UUID"):
        build_snapshot_zip({"a.csv": b"x"}, client_id)


def test_build_enforces_file_expanded_and_compressed_limits(monkeypatch) -> None:
    client_id = str(uuid.uuid4())
    monkeypatch.setattr(mqtt_snapshot, "MAX_FILE_BYTES", 1)
    with pytest.raises(SnapshotValidationError, match="file is too large"):
        build_snapshot_zip({"a.csv": b"xx"}, client_id)

    monkeypatch.setattr(mqtt_snapshot, "MAX_FILE_BYTES", 10)
    monkeypatch.setattr(mqtt_snapshot, "MAX_EXPORT_BYTES", 1)
    with pytest.raises(SnapshotValidationError, match="expanded"):
        build_snapshot_zip({"a.csv": b"x", "b.csv": b"x"}, client_id)

    monkeypatch.setattr(mqtt_snapshot, "MAX_EXPORT_BYTES", 10)
    monkeypatch.setattr(mqtt_snapshot, "MAX_SNAPSHOT_BYTES", 1)
    with pytest.raises(SnapshotValidationError, match="compressed"):
        build_snapshot_zip({"a.csv": b"x"}, client_id)


def test_reader_rejects_empty_missing_and_invalid_manifests() -> None:
    client_id = str(uuid.uuid4())
    files = {"a.csv": b"x"}
    cases = [
        (_custom_zip([]), "number"),
        (_custom_zip([("a.csv", b"x")]), "missing"),
        (_custom_zip([("a.csv", b"x"), ("manifest.json", b"\xff")]), "invalid"),
        (_custom_zip([("a.csv", b"x"), ("manifest.json", b"[]")]), "unsupported fields"),
        (
            _custom_zip(
                [
                    ("a.csv", b"x"),
                    (
                        "manifest.json",
                        _manifest(client_id, files, version=True),
                    ),
                ]
            ),
            "version",
        ),
        (
            _custom_zip(
                [
                    ("a.csv", b"x"),
                    (
                        "manifest.json",
                        _manifest(client_id, files, snapshot_hash=123),
                    ),
                ]
            ),
            "hash",
        ),
    ]

    for payload, message in cases:
        with pytest.raises(SnapshotValidationError, match=message):
            read_snapshot_zip(payload, client_id)


def test_reader_rejects_non_bytes_and_oversized_expansion(monkeypatch) -> None:
    client_id = str(uuid.uuid4())
    with pytest.raises(SnapshotValidationError, match="compressed"):
        read_snapshot_zip("not-bytes", client_id)

    files = {"a.csv": b"xx"}
    payload = _custom_zip(
        [("a.csv", files["a.csv"]), ("manifest.json", _manifest(client_id, files))]
    )
    monkeypatch.setattr(mqtt_snapshot, "MAX_FILE_BYTES", 1)
    with pytest.raises(SnapshotValidationError, match="entry is too large"):
        read_snapshot_zip(payload, client_id)
