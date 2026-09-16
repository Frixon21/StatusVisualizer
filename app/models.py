from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.lantopolog import MAX_EXPORT_BYTES, MAX_FILE_BYTES

NodeType = Literal[
    "unknown", "router", "switch", "access-point", "server",
    "workstation", "printer", "phone", "other",
]
IconType = Literal[
    "auto", "router", "switch", "access-point", "server",
    "workstation", "printer", "phone", "other",
]
NodeShape = Literal["icon", "card", "circle"]
LivenessState = Literal["online", "offline", "unknown"]
WORLD_POSITION_MIN = -100.0
WORLD_POSITION_MAX = 101.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class DeviceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    address: str = Field(default="", max_length=253)
    notes: str = Field(default="", max_length=2000)
    x: float = Field(ge=WORLD_POSITION_MIN, le=WORLD_POSITION_MAX)
    y: float = Field(ge=WORLD_POSITION_MIN, le=WORLD_POSITION_MAX)
    node_type: NodeType = "unknown"
    icon_type: IconType = "auto"
    node_shape: NodeShape = "icon"
    mac_address: str = Field(default="", max_length=32)
    locked: bool = True

    @field_validator("name", "address", "notes", "mac_address")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class DeviceRecord(DeviceInput):
    id: str
    source: str = "local"
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: str
    updated_at: str
    liveness_state: LivenessState = "unknown"
    liveness_checked_at: str | None = None
    liveness_latency_ms: float | None = None


class LivenessStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str
    address: str
    state: LivenessState
    checked_at: str | None = None
    latency_ms: float | None = None


class LivenessCheckInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EdgeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=100)
    target_id: str = Field(min_length=1, max_length=100)
    label: str = Field(default="", max_length=120)

    @model_validator(mode="after")
    def different_nodes(self) -> EdgeInput:
        if self.source_id == self.target_id:
            raise ValueError("A connection must join two different nodes")
        return self


class DevicePosition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=100)
    x: float = Field(ge=WORLD_POSITION_MIN, le=WORLD_POSITION_MAX)
    y: float = Field(ge=WORLD_POSITION_MIN, le=WORLD_POSITION_MAX)


class DevicePositionsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    positions: list[DevicePosition] = Field(min_length=1, max_length=500)

    @field_validator("positions")
    @classmethod
    def unique_devices(cls, positions: list[DevicePosition]) -> list[DevicePosition]:
        if len({position.id for position in positions}) != len(positions):
            raise ValueError("Each device may only appear once")
        return positions


class TopologyPdfInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_ids: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        min_length=1,
        max_length=500,
    )
    label_mode: Literal["hostname", "ip", "both"] = "both"
    vlan_view: bool = False
    theme: Literal["light", "dark"] = "dark"

    @field_validator("node_ids")
    @classmethod
    def unique_devices(cls, node_ids: list[str]) -> list[str]:
        if len(set(node_ids)) != len(node_ids):
            raise ValueError("Each device may only appear once")
        return node_ids


class EdgeRecord(EdgeInput):
    id: str
    kind: Literal["manual", "infrastructure", "attachment"] = "manual"
    source_interface: str = ""
    target_interface: str = ""
    evidence: str = ""
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class ImportFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=MAX_FILE_BYTES)


class LantopologImportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[ImportFileInput] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def within_import_budget(self) -> LantopologImportInput:
        total_size = sum(len(item.content.encode("utf-8")) for item in self.files)
        if total_size > MAX_EXPORT_BYTES:
            raise ValueError("The combined export is too large.")
        return self
