from __future__ import annotations

import csv
import hashlib
import html
import ipaddress
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from io import StringIO
from types import MappingProxyType
from typing import TypeVar
from xml.etree import ElementTree

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_EXPORT_BYTES = 20 * 1024 * 1024
MAX_EXPORT_FILES = 64

_T = TypeVar("_T")


class ExportValidationError(ValueError):
    """Raised when an uploaded Lantopolog export cannot be safely imported."""


@dataclass(frozen=True, slots=True)
class ImportedNode:
    key: str
    name: str
    address: str
    node_type: str
    mac_address: str
    metadata: Mapping[str, str]
    x: float = 0.5
    y: float = 0.5


@dataclass(frozen=True, slots=True)
class ImportedInterface:
    key: str
    node_key: str
    port: str
    if_index: str = ""
    name: str = ""
    admin_status: str = ""
    oper_status: str = ""
    speed_mbps: str = ""
    duplex_mode: str = ""
    stp_state: str = ""
    tagged_vlan: str = ""
    untagged_vlan: str = ""
    pvid_vlan: str = ""
    alias: str = ""
    metadata: Mapping[str, str] = MappingProxyType({})

    @property
    def switch_key(self) -> str:
        return self.node_key


@dataclass(frozen=True, slots=True)
class ImportedVlan:
    key: str
    vlan_id: str
    name: str
    switch_key: str
    switch_address: str
    tagged_ports: str
    untagged_ports: str
    metadata: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ImportedEdge:
    key: str
    source_key: str
    target_key: str
    source_interface_key: str | None
    target_interface_key: str | None
    label: str
    edge_type: str
    metadata: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ImportedTopology:
    nodes: tuple[ImportedNode, ...]
    edges: tuple[ImportedEdge, ...]
    interfaces: tuple[ImportedInterface, ...]
    vlans: tuple[ImportedVlan, ...]
    files_used: tuple[str, ...]
    summary: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class _SelectedFile:
    path: str
    text: str


@dataclass(frozen=True, slots=True)
class _XmlLayout:
    positions: Mapping[str, tuple[float, float]]
    links: tuple[tuple[str, str, str, Mapping[str, str]], ...]


_ROLE_NAMES: Mapping[str, tuple[str, str | None]] = MappingProxyType(
    {
        "switches": ("sw_list.csv", "swlist.csv"),
        "connections": ("sw_conn.csv", "swconn.csv"),
        "ports": ("port_list.csv", "portlist.csv"),
        "vlans": ("vlan_list.csv", "vlanlist.csv"),
        "endpoints_wide": ("complist.csv", None),
        "endpoints_compact": ("complist2.csv", None),
        "map": ("top_map.xml", None),
    }
)


def _frozen(values: Mapping[str, _T] | None = None) -> Mapping[str, _T]:
    return MappingProxyType(dict(values or {}))


def _clean(value: object) -> str:
    return str(value or "").strip().strip("\ufeff")


def _normalize_path(path: object) -> str:
    value = _clean(path).replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    parts = tuple(part for part in value.split("/") if part not in {"", "."})
    if not parts or any(part == ".." or "\x00" in part for part in parts):
        raise ExportValidationError("The export contains an invalid file path.")
    return "/".join(parts)


def _decode_content(content: object) -> tuple[str, int]:
    if isinstance(content, str):
        encoded = content.encode("utf-8")
        return content.lstrip("\ufeff"), len(encoded)
    if isinstance(content, (bytes, bytearray)):
        raw = bytes(content)
        try:
            return raw.decode("utf-8-sig"), len(raw)
        except UnicodeDecodeError:
            try:
                return raw.decode("cp1252"), len(raw)
            except UnicodeDecodeError as error:
                raise ExportValidationError("An export file has an unsupported encoding.") from error
    raise ExportValidationError("Every export file must contain text data.")


def _select_files(files: Mapping[str, object]) -> Mapping[str, _SelectedFile]:
    if not isinstance(files, Mapping) or not files:
        raise ExportValidationError("No recognized Lantopolog export files were provided.")
    if len(files) > MAX_EXPORT_FILES:
        raise ExportValidationError("The export contains too many files.")

    candidates: list[_SelectedFile] = []
    total_bytes = 0
    for supplied_path, content in files.items():
        path = _normalize_path(supplied_path)
        text, size = _decode_content(content)
        if size > MAX_FILE_BYTES:
            raise ExportValidationError(f"The export file {path.rsplit('/', 1)[-1]} is too large.")
        total_bytes += size
        if total_bytes > MAX_EXPORT_BYTES:
            raise ExportValidationError("The combined export is too large.")
        candidates.append(_SelectedFile(path=path, text=text))

    def ranked_matches(name: str, *, tmp_only: bool) -> list[_SelectedFile]:
        matches = []
        for item in candidates:
            parts = item.path.split("/")
            is_tmp = len(parts) >= 2 and parts[-2].casefold() == "tmp"
            if parts[-1].casefold() == name.casefold() and is_tmp == tmp_only:
                matches.append(item)
        return sorted(matches, key=lambda item: (item.path.count("/"), item.path.casefold()))

    selected: dict[str, _SelectedFile] = {}
    for role, (authoritative, fallback) in _ROLE_NAMES.items():
        matches = ranked_matches(authoritative, tmp_only=False)
        if matches:
            selected[role] = replace(matches[0], path=matches[0].path.rsplit("/", 1)[-1])
            continue
        if fallback:
            matches = ranked_matches(fallback, tmp_only=True)
            if matches:
                selected[role] = replace(
                    matches[0], path=f"Tmp/{matches[0].path.rsplit('/', 1)[-1]}"
                )

    if not selected:
        raise ExportValidationError("No recognized Lantopolog export files were provided.")
    if "switches" not in selected:
        raise ExportValidationError("A recognized switch list is required to import this export.")

    # complist.csv contains every compact field plus the richer inventory fields.
    if "endpoints_wide" in selected:
        selected.pop("endpoints_compact", None)
    return MappingProxyType(selected)


def _csv_rows(text: str, filename: str) -> tuple[dict[str, str], ...]:
    try:
        reader = csv.reader(StringIO(text, newline=""), delimiter=";", quotechar='"')
        raw_rows = list(reader)
    except (csv.Error, UnicodeError) as error:
        raise ExportValidationError(f"{filename} is not a valid semicolon-separated export.") from error
    if not raw_rows:
        raise ExportValidationError(f"{filename} is empty.")
    headers = tuple(_clean(value) for value in raw_rows[0])
    if not any(headers):
        raise ExportValidationError(f"{filename} has no recognizable columns.")
    records: list[dict[str, str]] = []
    for raw in raw_rows[1:]:
        if not any(_clean(value) for value in raw):
            continue
        record = {
            header: _clean(raw[index]) if index < len(raw) else ""
            for index, header in enumerate(headers)
            if header
        }
        records.append(record)
    return tuple(records)


def _metadata(record: Mapping[str, str]) -> Mapping[str, str]:
    return _frozen({key: cleaned for key, value in record.items() if (cleaned := _clean(value))})


def _normalize_mac(value: object) -> str:
    compact = re.sub(r"[^0-9A-Fa-f]", "", _clean(value))
    if len(compact) != 12:
        return ""
    return ":".join(compact[index : index + 2] for index in range(0, 12, 2)).upper()


def _valid_address(value: object) -> str:
    candidate = _clean(value)
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return ""


def _switch_key(address: str) -> str:
    return f"switch:{address}"


def _interface_key(node_key: str, port: object) -> str:
    return f"{node_key}:port:{_clean(port)}"


def _infer_switch_type(record: Mapping[str, str]) -> str:
    text = " ".join(
        _clean(record.get(field, "")) for field in ("Name", "Model", "Description")
    ).casefold()
    if re.search(r"\b(router|gateway|firewall|udm(?:-pro)?)\b", text):
        return "router"
    if re.search(r"\b(access[ -]?point|wireless ap)\b", text):
        return "access-point"
    if re.search(r"\b(truenas|server|storage server|nas)\b", text):
        return "server"
    return "switch"


def _infer_endpoint_type(record: Mapping[str, str]) -> str:
    vendor = _clean(record.get("MAC Lookup Vendor", "")).casefold()
    hostname = _clean(record.get("HostName", "")).casefold()
    model = _clean(record.get("Model", "")).casefold()
    os_caption = _clean(record.get("OS Caption", "")).casefold()
    combined = f"{vendor} {hostname} {model} {os_caption}"
    if any(token in combined for token in ("mitel", "shoretel", "yealink", "polycom")):
        return "phone"
    if "phone" in hostname or "voip" in combined:
        return "phone"
    if any(token in combined for token in ("printer", "lexmark", "brother industries")):
        return "printer"
    if hostname.startswith("npi") and "hewlett" in vendor:
        return "printer"
    if any(token in hostname for token in ("server", "hyperv", "-svr", "-srv")):
        return "server"
    if "windows server" in os_caption:
        return "server"
    if os_caption and any(token in os_caption for token in ("windows", "macos", "ubuntu desktop")):
        return "workstation"
    if hostname.startswith(("desktop", "pc-", "laptop", "remote")):
        return "workstation"
    return "unknown"


def _parse_switches(source: _SelectedFile) -> tuple[ImportedNode, ...]:
    rows = _csv_rows(source.text, source.path)
    required = {"IP", "Name"}
    if not rows or not required.issubset(rows[0]):
        raise ExportValidationError("The recognized switch list has an invalid header or no devices.")
    nodes: dict[str, ImportedNode] = {}
    for row in rows:
        address = _valid_address(row.get("IP"))
        if not address:
            continue
        key = _switch_key(address)
        name = _clean(row.get("Name")) or address
        candidate = ImportedNode(
            key=key,
            name=name,
            address=address,
            node_type=_infer_switch_type(row),
            mac_address=_normalize_mac(row.get("MAC address")),
            metadata=_metadata(row),
        )
        previous = nodes.get(key)
        if previous is None or len(candidate.metadata) > len(previous.metadata):
            nodes[key] = candidate
    if not nodes:
        raise ExportValidationError("The recognized switch list contains no valid switch addresses.")
    return tuple(nodes[key] for key in sorted(nodes))


def _merge_metadata(left: Mapping[str, str], right: Mapping[str, str]) -> Mapping[str, str]:
    merged = dict(left)
    for key, value in right.items():
        if value and (key not in merged or len(value) > len(merged[key])):
            merged[key] = value
    return _frozen(merged)


def _parse_endpoints(source: _SelectedFile) -> tuple[ImportedNode, ...]:
    rows = _csv_rows(source.text, source.path)
    if not rows or not ({"MAC", "IP", "HostName"} & set(rows[0])):
        raise ExportValidationError("The recognized computer list has an invalid header or no devices.")
    nodes: dict[str, ImportedNode] = {}
    for row in rows:
        mac = _normalize_mac(row.get("MAC"))
        address = _valid_address(row.get("IP"))
        hostname = _clean(row.get("HostName"))
        if not (mac or address or hostname):
            continue
        if mac:
            key = f"endpoint:mac:{mac}"
        elif address:
            key = f"endpoint:ip:{address}"
        else:
            digest = hashlib.sha256(hostname.casefold().encode("utf-8")).hexdigest()[:16]
            key = f"endpoint:name:{digest}"
        vendor = _clean(row.get("MAC Lookup Vendor"))
        name = hostname or address or vendor or mac
        candidate = ImportedNode(
            key=key,
            name=name,
            address=address,
            node_type=_infer_endpoint_type(row),
            mac_address=mac,
            metadata=_metadata(row),
        )
        previous = nodes.get(key)
        if previous is None:
            nodes[key] = candidate
        else:
            nodes[key] = replace(
                previous,
                name=previous.name or candidate.name,
                address=previous.address or candidate.address,
                node_type=(
                    previous.node_type
                    if previous.node_type != "unknown"
                    else candidate.node_type
                ),
                mac_address=previous.mac_address or candidate.mac_address,
                metadata=_merge_metadata(previous.metadata, candidate.metadata),
            )
    return tuple(nodes[key] for key in sorted(nodes))


_SECTION_RE = re.compile(r"^switch\s+(\S+)(?:\s+.*)?$", re.IGNORECASE)


def _parse_interfaces(source: _SelectedFile) -> tuple[ImportedInterface, ...]:
    try:
        rows = tuple(csv.reader(StringIO(source.text, newline=""), delimiter=";", quotechar='"'))
    except csv.Error as error:
        raise ExportValidationError(f"{source.path} is not a valid port list.") from error
    if not rows:
        raise ExportValidationError(f"{source.path} is empty.")
    headers = tuple(_clean(value) for value in rows[0])
    if "Port" not in headers:
        raise ExportValidationError("The recognized port list has an invalid header.")
    current_switch = ""
    interfaces: dict[str, ImportedInterface] = {}
    for raw in rows[1:]:
        values = tuple(_clean(value) for value in raw)
        nonempty = tuple(value for value in values if value)
        if len(nonempty) == 1:
            match = _SECTION_RE.match(nonempty[0])
            current_switch = _valid_address(match.group(1)) if match else ""
            continue
        if not current_switch or not values or not values[0]:
            continue
        record = {
            header: values[index] if index < len(values) else ""
            for index, header in enumerate(headers)
            if header
        }
        node_key = _switch_key(current_switch)
        port = record.get("Port", "")
        key = _interface_key(node_key, port)
        interfaces[key] = ImportedInterface(
            key=key,
            node_key=node_key,
            port=port,
            if_index=record.get("IfIndex", ""),
            name=record.get("Name", "") or port,
            admin_status=record.get("Admin Status", ""),
            oper_status=record.get("Oper Status", ""),
            speed_mbps=record.get("Speed", ""),
            duplex_mode=record.get("DuplexMode", ""),
            stp_state=record.get("STPstate", ""),
            tagged_vlan=record.get("Tagged VLAN", ""),
            untagged_vlan=record.get("Untagged VLAN", ""),
            pvid_vlan=record.get("PVID VLAN", ""),
            alias=record.get("Alias", ""),
            metadata=_metadata(record),
        )
    return tuple(interfaces[key] for key in sorted(interfaces))


def _parse_vlans(source: _SelectedFile) -> tuple[ImportedVlan, ...]:
    rows = _csv_rows(source.text, source.path)
    if rows and "VLAN ID" not in rows[0]:
        raise ExportValidationError("The recognized VLAN list has an invalid header.")
    vlans: dict[str, ImportedVlan] = {}
    for row in rows:
        vlan_id = _clean(row.get("VLAN ID"))
        switch_field = _clean(row.get("Switch"))
        address = _valid_address(switch_field.split(maxsplit=1)[0] if switch_field else "")
        if not vlan_id or not address:
            continue
        switch_key = _switch_key(address)
        key = f"vlan:{address}:{vlan_id}"
        vlans[key] = ImportedVlan(
            key=key,
            vlan_id=vlan_id,
            name=_clean(row.get("VLAN Name")) or f"VLAN {vlan_id}",
            switch_key=switch_key,
            switch_address=address,
            tagged_ports=_clean(row.get("Tagged(trunk)ports")),
            untagged_ports=_clean(row.get("Untagged(access)ports")),
            metadata=_metadata(row),
        )
    return tuple(vlans[key] for key in sorted(vlans))


def _edge_key(
    source_key: str, source_port: str, target_key: str, target_port: str, edge_type: str
) -> str:
    material = f"{source_key}\x1f{source_port}\x1f{target_key}\x1f{target_port}\x1f{edge_type}"
    return f"edge:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def _canonical_connection(
    left_key: str, left_port: str, right_key: str, right_port: str
) -> tuple[str, str, str, str]:
    if (left_key, left_port) <= (right_key, right_port):
        return left_key, _clean(left_port), right_key, _clean(right_port)
    return right_key, _clean(right_port), left_key, _clean(left_port)


def _usable_port(value: object) -> str:
    port = _clean(value)
    return (
        ""
        if port.casefold() in {"", "-", "?", "demo", "xx"}
        or re.fullmatch(r"0+", port)
        else port
    )


def _make_infrastructure_edge(
    left_key: str,
    left_port: str,
    right_key: str,
    right_port: str,
    metadata: Mapping[str, str],
) -> ImportedEdge | None:
    source_key, source_port, target_key, target_port = _canonical_connection(
        left_key, left_port, right_key, right_port
    )
    if source_key == target_key:
        return None
    source_interface = _interface_key(source_key, source_port) if _usable_port(source_port) else None
    target_interface = _interface_key(target_key, target_port) if _usable_port(target_port) else None
    return ImportedEdge(
        key=_edge_key(source_key, source_port, target_key, target_port, "infrastructure"),
        source_key=source_key,
        target_key=target_key,
        source_interface_key=source_interface,
        target_interface_key=target_interface,
        label=" - ".join(value for value in (source_port, target_port) if value),
        edge_type="infrastructure",
        metadata=metadata,
    )


def _parse_connections(
    source: _SelectedFile,
    switch_addresses_by_name: Mapping[str, str],
) -> tuple[ImportedEdge, ...]:
    try:
        rows = tuple(csv.reader(StringIO(source.text, newline=""), delimiter=";", quotechar='"'))
    except csv.Error as error:
        raise ExportValidationError(f"{source.path} is not a valid connection list.") from error
    if not rows or len(rows[0]) < 8 or _clean(rows[0][2]).casefold() != "ip":
        raise ExportValidationError("The recognized connection list has an invalid header.")
    edges: dict[str, ImportedEdge] = {}
    for raw in rows[1:]:
        values = tuple(_clean(value) for value in raw) + ("",) * max(0, 9 - len(raw))
        left_address = _valid_address(values[2]) or switch_addresses_by_name.get(
            values[0].casefold(), ""
        )
        right_address = _valid_address(values[6]) or switch_addresses_by_name.get(
            values[7].casefold(), ""
        )
        if not left_address or not right_address:
            continue
        source_key, source_port, target_key, target_port = _canonical_connection(
            _switch_key(left_address), values[3], _switch_key(right_address), values[5]
        )
        if source_key == _switch_key(left_address):
            source_name, source_location = values[0], values[1]
            target_name, target_location = values[7], values[8]
        else:
            source_name, source_location = values[7], values[8]
            target_name, target_location = values[0], values[1]
        edge = _make_infrastructure_edge(
            source_key,
            source_port,
            target_key,
            target_port,
            _frozen(
                {
                    "Source Name": source_name,
                    "Source Location": source_location,
                    "Source IP": source_key.removeprefix("switch:"),
                    "Source Port": source_port,
                    "Target Port": target_port,
                    "Target IP": target_key.removeprefix("switch:"),
                    "Target Name": target_name,
                    "Target Location": target_location,
                    "Source File": source.path,
                }
            ),
        )
        if edge is None:
            continue
        previous = edges.get(edge.key)
        edges[edge.key] = (
            edge
            if previous is None
            else replace(previous, metadata=_merge_metadata(previous.metadata, edge.metadata))
        )
    return tuple(edges[key] for key in sorted(edges))


_CONNECTED_RE = re.compile(r"^sw\s+(\S+)\s+port\s+(.+?)\s*$", re.IGNORECASE)


def _endpoint_attachment(node: ImportedNode) -> tuple[str, str]:
    connected_to = _clean(node.metadata.get("Connected to", ""))
    match = _CONNECTED_RE.match(connected_to)
    if not match:
        return "", ""
    return _valid_address(match.group(1)), _clean(match.group(2))


def _is_fanout_candidate(endpoint: ImportedNode, port: str) -> bool:
    return bool(
        _usable_port(port)
        and not re.fullmatch(r"0+", port)
        and (endpoint.mac_address or endpoint.address)
    )


def _fanout_key(switch_address: str, port: str) -> str:
    material = f"{switch_address.casefold()}\x1f{port.casefold()}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"switch:fanout:{digest}"


def _add_endpoint_edges_and_interfaces(
    endpoints: Iterable[ImportedNode], interfaces: Iterable[ImportedInterface]
) -> tuple[
    tuple[ImportedEdge, ...],
    tuple[ImportedInterface, ...],
    tuple[ImportedNode, ...],
]:
    by_key = {item.key: item for item in interfaces}
    edges: dict[str, ImportedEdge] = {}
    attachments: list[tuple[ImportedNode, str, str]] = []
    fanout_groups: dict[tuple[str, str], list[ImportedNode]] = {}
    for endpoint in sorted(endpoints, key=lambda item: item.key):
        switch_address, port = _endpoint_attachment(endpoint)
        if not switch_address or not port:
            continue
        attachments.append((endpoint, switch_address, port))
        if _is_fanout_candidate(endpoint, port):
            fanout_groups.setdefault((switch_address, port), []).append(endpoint)

    repeated_groups = {
        attachment: members
        for attachment, members in fanout_groups.items()
        if len(members) > 1
    }
    synthetic_nodes: dict[str, ImportedNode] = {}
    for (switch_address, port), members in sorted(repeated_groups.items()):
        switch_key = _switch_key(switch_address)
        fanout_key = _fanout_key(switch_address, port)
        endpoint_count = str(len(members))
        port_aliases = sorted(
            {
                value
                for endpoint in members
                if (value := _clean(endpoint.metadata.get("Port Name, Alias", "")))
            }
        )
        vlans = sorted(
            {
                value
                for endpoint in members
                if (value := _clean(endpoint.metadata.get("VLAN", "")))
            }
        )
        synthetic_nodes[fanout_key] = ImportedNode(
            key=fanout_key,
            name=f"Inferred switch/bridge on {switch_address} port {port}",
            address="",
            node_type="switch",
            mac_address="",
            metadata=_frozen(
                {
                    "Synthetic Role": "shared-port-fanout",
                    "Synthesized From": "multiple endpoint attachments",
                    "Inference": "Multiple endpoint identities observed behind one managed port; may be a physical switch or virtual bridge",
                    "Managed Switch": switch_address,
                    "Managed Switch Key": switch_key,
                    "Managed Port": port,
                    "Endpoint Count": endpoint_count,
                    "Port Name, Alias": " | ".join(port_aliases),
                    "VLANs": ", ".join(vlans),
                }
            ),
        )
        managed_interface_key = _interface_key(switch_key, port)
        first_endpoint = members[0]
        if managed_interface_key not in by_key:
            port_alias = _clean(first_endpoint.metadata.get("Port Name, Alias", ""))
            by_key[managed_interface_key] = ImportedInterface(
                key=managed_interface_key,
                node_key=switch_key,
                port=port,
                name=(port_alias.split(",", 1)[0].strip() or port),
                speed_mbps=_clean(first_endpoint.metadata.get("Port Speed", "")),
                pvid_vlan=_clean(first_endpoint.metadata.get("VLAN", "")),
                alias=(port_alias.split(",", 1)[1].strip() if "," in port_alias else ""),
                metadata=_frozen(
                    {
                        "Synthesized From": "computer list",
                        "Connected to": _clean(
                            first_endpoint.metadata.get("Connected to", "")
                        ),
                        "Port Name, Alias": port_alias,
                    }
                ),
            )
        by_key[managed_interface_key] = replace(
            by_key[managed_interface_key],
            metadata=_merge_metadata(
                by_key[managed_interface_key].metadata,
                _frozen(
                    {
                        "Fan-out Node": fanout_key,
                        "Attached Endpoint Count": endpoint_count,
                    }
                ),
            ),
        )
        uplink_interface_key = _interface_key(fanout_key, "uplink")
        by_key[uplink_interface_key] = ImportedInterface(
            key=uplink_interface_key,
            node_key=fanout_key,
            port="uplink",
            name="uplink",
            alias=f"To {switch_address} port {port}",
            metadata=_frozen(
                {
                    "Synthetic Role": "fanout-uplink",
                    "Synthesized From": "multiple endpoint attachments",
                    "Peer": switch_address,
                    "Peer Port": port,
                    "Endpoint Count": endpoint_count,
                }
            ),
        )
        uplink_edge_key = _edge_key(
            switch_key, port, fanout_key, "uplink", "infrastructure"
        )
        edges[uplink_edge_key] = ImportedEdge(
            key=uplink_edge_key,
            source_key=switch_key,
            target_key=fanout_key,
            source_interface_key=managed_interface_key,
            target_interface_key=uplink_interface_key,
            label=port_aliases[0] if port_aliases else port,
            edge_type="infrastructure",
            metadata=_frozen(
                {
                    "Synthetic Role": "fanout-uplink",
                    "Synthesized From": "multiple endpoint attachments",
                    "Managed Switch": switch_address,
                    "Managed Port": port,
                    "Endpoint Count": endpoint_count,
                    "Port Name, Alias": " | ".join(port_aliases),
                    "VLANs": ", ".join(vlans),
                }
            ),
        )

    for endpoint, switch_address, port in attachments:
        switch_key = _switch_key(switch_address)
        interface_key = _interface_key(switch_key, port) if _usable_port(port) else None
        if interface_key and interface_key not in by_key:
            port_alias = _clean(endpoint.metadata.get("Port Name, Alias", ""))
            by_key[interface_key] = ImportedInterface(
                key=interface_key,
                node_key=switch_key,
                port=port,
                name=(port_alias.split(",", 1)[0].strip() or port),
                speed_mbps=_clean(endpoint.metadata.get("Port Speed", "")),
                pvid_vlan=_clean(endpoint.metadata.get("VLAN", "")),
                alias=(port_alias.split(",", 1)[1].strip() if "," in port_alias else ""),
                metadata=_frozen(
                    {
                        "Synthesized From": "computer list",
                        "Connected to": _clean(endpoint.metadata.get("Connected to", "")),
                        "Port Name, Alias": port_alias,
                    }
                ),
            )
        fanout_members = repeated_groups.get((switch_address, port), ())
        uses_fanout = endpoint in fanout_members
        source_key = _fanout_key(switch_address, port) if uses_fanout else switch_key
        source_interface_key = None if uses_fanout else interface_key
        key = _edge_key(source_key, port, endpoint.key, "", "attachment")
        edges[key] = ImportedEdge(
            key=key,
            source_key=source_key,
            target_key=endpoint.key,
            source_interface_key=source_interface_key,
            target_interface_key=None,
            label=(
                "Inferred downstream attachment"
                if uses_fanout
                else _clean(endpoint.metadata.get("Port Name, Alias", "")) or port
            ),
            edge_type="attachment",
            metadata=_frozen(
                {
                    "Connected to": _clean(endpoint.metadata.get("Connected to", "")),
                    "VLAN": _clean(endpoint.metadata.get("VLAN", "")),
                    "Port Speed": _clean(endpoint.metadata.get("Port Speed", "")),
                    "Original Managed Switch": switch_address,
                    "Original Managed Port": port,
                    "Inferred Fan-out": source_key if uses_fanout else "",
                }
            ),
        )
    return (
        tuple(edges[key] for key in sorted(edges)),
        tuple(by_key[key] for key in sorted(by_key)),
        tuple(synthetic_nodes[key] for key in sorted(synthetic_nodes)),
    )


def _port_from_interface_key(interface_key: str | None) -> str:
    return interface_key.rsplit(":port:", 1)[-1] if interface_key else ""


def _infrastructure_ports(edge: ImportedEdge) -> tuple[str, str]:
    label_ports = (
        tuple(_clean(value) for value in edge.label.split(" - ", 1))
        if " - " in edge.label
        else (_clean(edge.label), "")
    )
    return (
        _usable_port(_port_from_interface_key(edge.source_interface_key))
        or _usable_port(edge.metadata.get("Source Port", ""))
        or _usable_port(label_ports[0]),
        _usable_port(_port_from_interface_key(edge.target_interface_key))
        or _usable_port(edge.metadata.get("Target Port", ""))
        or _usable_port(label_ports[1]),
    )


def _resolve_infrastructure_interfaces(
    edges: Iterable[ImportedEdge], interfaces: Iterable[ImportedInterface]
) -> tuple[tuple[ImportedEdge, ...], tuple[ImportedInterface, ...]]:
    by_key = {interface.key: interface for interface in interfaces}
    resolved_edges: list[ImportedEdge] = []
    for edge in edges:
        if edge.edge_type != "infrastructure":
            resolved_edges.append(edge)
            continue
        source_port, target_port = _infrastructure_ports(edge)
        edge_metadata = edge.metadata

        def ensure_interface(
            node_key: str,
            port: str,
            peer_key: str,
            metadata: Mapping[str, str] = edge_metadata,
        ) -> str | None:
            if not _usable_port(port):
                return None
            key = _interface_key(node_key, port)
            if key not in by_key:
                sources = [
                    name
                    for name in (
                        metadata.get("Source File", ""),
                        "top_map.xml" if metadata.get("XML Cell") else "",
                    )
                    if name
                ]
                by_key[key] = ImportedInterface(
                    key=key,
                    node_key=node_key,
                    port=port,
                    name=port,
                    alias=f"Uplink to {peer_key.removeprefix('switch:')}",
                    metadata=_merge_metadata(
                        metadata,
                        _frozen(
                            {
                                "Synthesized From": ", ".join(dict.fromkeys(sources))
                                or "infrastructure link",
                                "Peer": peer_key.removeprefix("switch:"),
                                "Port Label": port,
                            }
                        ),
                    ),
                )
            return key

        source_interface_key = ensure_interface(edge.source_key, source_port, edge.target_key)
        target_interface_key = ensure_interface(edge.target_key, target_port, edge.source_key)
        resolved_edges.append(
            replace(
                edge,
                source_interface_key=source_interface_key,
                target_interface_key=target_interface_key,
            )
        )
    return tuple(resolved_edges), tuple(by_key[key] for key in sorted(by_key))


_IP_IN_TEXT_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")


def _extract_address(value: object) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(_clean(value)).replace("&nbsp;", " "))
    for candidate in _IP_IN_TEXT_RE.findall(text):
        if address := _valid_address(candidate):
            return address
    return ""


def _split_edge_label(value: object) -> tuple[str, str]:
    label = re.sub(r"<[^>]+>", " ", html.unescape(_clean(value))).strip().rstrip("-").strip()
    if not label:
        return "", ""
    if "-" not in label:
        return label, ""
    left, right = label.rsplit("-", 1)
    return _clean(left), _clean(right)


def _parse_xml_layout(source: _SelectedFile) -> _XmlLayout:
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", source.text, re.IGNORECASE):
        raise ExportValidationError("The topology map contains unsupported XML declarations.")
    try:
        root = ElementTree.fromstring(source.text)
    except ElementTree.ParseError as error:
        raise ExportValidationError("The recognized topology map is not valid XML.") from error

    cells = {cell.get("id", ""): cell for cell in root.iter("mxCell") if cell.get("id")}
    cell_addresses: dict[str, str] = {}
    raw_positions: dict[str, tuple[float, float]] = {}
    max_x = max_y = 1.0
    for cell_id, cell in cells.items():
        if cell.get("vertex") != "1" or cell.get("parent") != "1":
            continue
        address = _extract_address(cell.get("value", ""))
        geometry = cell.find("mxGeometry")
        if not address or geometry is None:
            continue
        try:
            x = float(geometry.get("x", "0"))
            y = float(geometry.get("y", "0"))
            width = float(geometry.get("width", "0"))
            height = float(geometry.get("height", "0"))
        except ValueError:
            continue
        cell_addresses[cell_id] = address
        raw_positions[address] = (x, y)
        max_x = max(max_x, x + max(width, 1.0))
        max_y = max(max_y, y + max(height, 1.0))

    try:
        page_width = float(root.get("pageWidth", "0"))
        page_height = float(root.get("pageHeight", "0"))
    except ValueError:
        page_width = page_height = 0.0
    canvas_width = page_width if page_width > 0 else max_x
    canvas_height = page_height if page_height > 0 else max_y
    positions = {
        address: (
            min(0.96, max(0.04, x / canvas_width)),
            min(0.96, max(0.04, y / canvas_height)),
        )
        for address, (x, y) in raw_positions.items()
    }

    xml_links: list[tuple[str, str, str, Mapping[str, str]]] = []
    for cell_id, cell in cells.items():
        source_id, target_id = cell.get("source", ""), cell.get("target", "")
        if cell.get("edge") != "1" or source_id not in cell_addresses or target_id not in cell_addresses:
            continue
        label_cell = next(
            (candidate for candidate in cells.values() if candidate.get("parent") == cell_id),
            None,
        )
        label = _clean(label_cell.get("value", "")) if label_cell is not None else ""
        xml_links.append(
            (
                cell_addresses[source_id],
                cell_addresses[target_id],
                label,
                _frozen({"XML Cell": cell_id, "XML Label": label, "Source File": source.path}),
            )
        )
    return _XmlLayout(positions=_frozen(positions), links=tuple(xml_links))


def _layout_nodes(
    nodes: Iterable[ImportedNode],
    edges: Iterable[ImportedEdge],
    xml_positions: Mapping[str, tuple[float, float]],
) -> tuple[ImportedNode, ...]:
    ordered = sorted(nodes, key=lambda node: node.key)
    switches = [node for node in ordered if node.key.startswith("switch:")]
    fanout_switches = [
        node
        for node in switches
        if node.metadata.get("Synthetic Role") == "shared-port-fanout"
    ]
    managed_switches = [node for node in switches if node not in fanout_switches]
    switch_positions: dict[str, tuple[float, float]] = {}
    missing_switches = [
        node for node in managed_switches if node.address not in xml_positions
    ]
    for node in managed_switches:
        if node.address in xml_positions:
            switch_positions[node.key] = xml_positions[node.address]
    columns = max(1, math.ceil(math.sqrt(len(missing_switches))))
    for index, node in enumerate(missing_switches):
        switch_positions[node.key] = (
            (index % columns + 1) / (columns + 1),
            0.12 + 0.18 * (index // columns),
        )

    fanouts_by_parent: dict[str, list[ImportedNode]] = {}
    for node in fanout_switches:
        parent_key = _clean(node.metadata.get("Managed Switch Key", ""))
        fanouts_by_parent.setdefault(parent_key, []).append(node)
    for parent_key, children in sorted(fanouts_by_parent.items()):
        parent_x, parent_y = switch_positions.get(parent_key, (0.5, 0.3))
        ordered_children = sorted(children, key=lambda child: child.key)
        for index, child in enumerate(ordered_children):
            angle = math.pi * (index + 1) / (len(ordered_children) + 1)
            switch_positions[child.key] = (
                min(0.96, max(0.04, parent_x + 0.11 * math.cos(angle))),
                min(0.96, max(0.04, parent_y + 0.1 + 0.025 * math.sin(angle))),
            )

    attached_to: dict[str, str] = {}
    for edge in edges:
        if edge.edge_type == "attachment":
            attached_to[edge.target_key] = edge.source_key
    groups: dict[str, list[ImportedNode]] = {}
    unattached: list[ImportedNode] = []
    for node in ordered:
        if node.key.startswith("switch:"):
            continue
        parent = attached_to.get(node.key, "")
        if parent:
            groups.setdefault(parent, []).append(node)
        else:
            unattached.append(node)

    endpoint_positions: dict[str, tuple[float, float]] = {}
    for switch_key, children in sorted(groups.items()):
        center_x, center_y = switch_positions.get(switch_key, (0.5, 0.35))
        for index, node in enumerate(sorted(children, key=lambda child: child.key)):
            ring = index // 10
            angle = (2 * math.pi * (index % 10) / min(10, len(children))) + math.pi / 2
            radius = 0.12 + ring * 0.07
            endpoint_positions[node.key] = (
                min(0.96, max(0.04, center_x + radius * math.cos(angle))),
                min(0.96, max(0.04, center_y + radius * math.sin(angle))),
            )
    unattached_columns = max(1, math.ceil(math.sqrt(len(unattached))))
    for index, node in enumerate(sorted(unattached, key=lambda child: child.key)):
        endpoint_positions[node.key] = (
            (index % unattached_columns + 1) / (unattached_columns + 1),
            min(0.94, 0.7 + 0.12 * (index // unattached_columns)),
        )

    positioned = []
    for node in ordered:
        x, y = switch_positions.get(node.key, endpoint_positions.get(node.key, (0.5, 0.5)))
        positioned.append(replace(node, x=round(x, 6), y=round(y, 6)))
    return tuple(positioned)


def parse_lantopolog_export(files: Mapping[str, object]) -> ImportedTopology:
    """Parse one uploaded Lantopolog Export folder into an immutable topology."""

    selected = _select_files(files)
    switches = _parse_switches(selected["switches"])
    endpoints = (
        _parse_endpoints(selected["endpoints_wide"])
        if "endpoints_wide" in selected
        else _parse_endpoints(selected["endpoints_compact"])
        if "endpoints_compact" in selected
        else ()
    )
    interfaces = _parse_interfaces(selected["ports"]) if "ports" in selected else ()
    endpoint_edges, interfaces, fanout_nodes = _add_endpoint_edges_and_interfaces(
        endpoints, interfaces
    )
    interface_keys = {interface.key for interface in interfaces}
    switch_names: dict[str, str] = {}
    duplicate_switch_names: set[str] = set()
    for switch in switches:
        normalized_name = switch.name.casefold()
        if normalized_name in switch_names:
            duplicate_switch_names.add(normalized_name)
        else:
            switch_names[normalized_name] = switch.address
    for duplicate_name in duplicate_switch_names:
        switch_names.pop(duplicate_name, None)
    infrastructure_edges = (
        _parse_connections(selected["connections"], _frozen(switch_names))
        if "connections" in selected
        else ()
    )
    vlans = _parse_vlans(selected["vlans"]) if "vlans" in selected else ()
    xml_layout = (
        _parse_xml_layout(selected["map"])
        if "map" in selected
        else _XmlLayout(positions=_frozen(), links=())
    )

    all_nodes = {node.key: node for node in (*switches, *endpoints, *fanout_nodes)}
    for edge in (*infrastructure_edges, *endpoint_edges):
        for key in (edge.source_key, edge.target_key):
            if key.startswith("switch:") and key not in all_nodes:
                address = key.removeprefix("switch:")
                all_nodes[key] = ImportedNode(
                    key=key,
                    name=address,
                    address=address,
                    node_type="switch",
                    mac_address="",
                    metadata=_frozen({"Synthesized From": "connection data"}),
                )

    edge_by_key = {edge.key: edge for edge in infrastructure_edges}
    for left_address, right_address, label, metadata in xml_layout.links:
        left_port, right_port = _split_edge_label(label)
        normal_matches = sum(
            key in interface_keys
            for key in (
                _interface_key(_switch_key(left_address), left_port),
                _interface_key(_switch_key(right_address), right_port),
            )
        )
        swapped_matches = sum(
            key in interface_keys
            for key in (
                _interface_key(_switch_key(left_address), right_port),
                _interface_key(_switch_key(right_address), left_port),
            )
        )
        if swapped_matches > normal_matches:
            left_port, right_port = right_port, left_port
        edge = _make_infrastructure_edge(
            _switch_key(left_address),
            left_port,
            _switch_key(right_address),
            right_port,
            metadata,
        )
        if edge is None:
            continue
        existing = edge_by_key.get(edge.key)
        replace_placeholder = False
        if existing is None:
            port_pair = sorted(_split_edge_label(label))
            compatible = [
                candidate
                for candidate in edge_by_key.values()
                if (candidate.source_key, candidate.target_key)
                == (edge.source_key, edge.target_key)
                and sorted(_split_edge_label(candidate.label)) == port_pair
            ]
            existing = compatible[0] if len(compatible) == 1 else None
        if existing is None:
            same_nodes = [
                candidate
                for candidate in edge_by_key.values()
                if (candidate.source_key, candidate.target_key)
                == (edge.source_key, edge.target_key)
            ]
            if len(same_nodes) == 1 and all(
                port.casefold() in {"", "demo", "xx", "?", "unknown"}
                for port in _infrastructure_ports(same_nodes[0])
            ):
                existing = same_nodes[0]
                replace_placeholder = True
        if existing is None:
            edge_by_key[edge.key] = edge
        elif replace_placeholder:
            edge_by_key.pop(existing.key)
            edge_by_key[edge.key] = replace(
                edge, metadata=_merge_metadata(existing.metadata, edge.metadata)
            )
        else:
            edge_by_key[existing.key] = replace(
                existing, metadata=_merge_metadata(existing.metadata, edge.metadata)
            )
        for address in (left_address, right_address):
            key = _switch_key(address)
            if key not in all_nodes:
                all_nodes[key] = ImportedNode(
                    key=key,
                    name=address,
                    address=address,
                    node_type="switch",
                    mac_address="",
                    metadata=_frozen({"Synthesized From": "topology map"}),
                )

    infrastructure_edges, interfaces = _resolve_infrastructure_interfaces(
        edge_by_key.values(), interfaces
    )
    all_edges = tuple(
        sorted((*infrastructure_edges, *endpoint_edges), key=lambda edge: edge.key)
    )
    nodes = _layout_nodes(all_nodes.values(), all_edges, xml_layout.positions)
    files_used = tuple(item.path for item in selected.values())
    switch_keys = {node.key for node in nodes if node.key.startswith("switch:")}
    endpoint_keys = {node.key for node in nodes if node.key.startswith("endpoint:")}
    inferred_switch_keys = {
        node.key
        for node in nodes
        if node.metadata.get("Synthetic Role") == "shared-port-fanout"
    }
    summary = _frozen(
        {
            "nodes": len(nodes),
            "switches": len(switch_keys),
            "inferred_switches": len(inferred_switch_keys),
            "endpoints": len(endpoint_keys),
            "connections": len(all_edges),
            "interfaces": len(interfaces),
            "vlans": len(vlans),
            "files_used": len(files_used),
        }
    )
    return ImportedTopology(
        nodes=nodes,
        edges=all_edges,
        interfaces=interfaces,
        vlans=vlans,
        files_used=files_used,
        summary=summary,
    )


__all__ = [
    "ExportValidationError",
    "ImportedEdge",
    "ImportedInterface",
    "ImportedNode",
    "ImportedTopology",
    "ImportedVlan",
    "parse_lantopolog_export",
]
