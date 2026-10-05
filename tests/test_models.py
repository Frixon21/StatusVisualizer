from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models import (
    DeviceInput,
    DevicePosition,
    DevicePositionsInput,
    DeviceRecord,
    EdgeInput,
    LivenessCheckInput,
    MqttClientUpdateInput,
    TopologyPdfInput,
)


def test_device_input_strips_text_and_normalizes_vlan_metadata() -> None:
    model = DeviceInput(
        name="  Core switch  ",
        address=" 192.168.1.2 ",
        notes="  Rack A  ",
        mac_address=" aa-bb ",
        x=-100,
        y=101,
        metadata={
            "VLAN": " 20 ",
            "VLANs": "30; 10,30",
            " Empty ": " ",
            "Ignored": None,
            "Owner": 42,
        },
    )

    assert model.name == "Core switch"
    assert model.address == "192.168.1.2"
    assert model.notes == "Rack A"
    assert model.mac_address == "aa-bb"
    assert model.metadata == {
        "VLAN": "20",
        "VLANs": "10, 30",
        "Owner": "42",
    }


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        ({"": "value"}, "keys"),
        ({"x" * 81: "value"}, "keys"),
        ({"VLAN": ""}, "blank"),
        ({"VLAN": "10,20"}, "exactly one"),
        ({"VLANs": "0,20"}, "1 to 4094"),
        ({"VLANs": "20,4095"}, "1 to 4094"),
        ({"VLANs": "ten"}, "whole numbers"),
        ({"Owner": "x" * 501}, "500"),
        ({f"key-{index}": index for index in range(41)}, "at most 40"),
    ],
)
def test_device_input_rejects_invalid_metadata(metadata, message) -> None:
    with pytest.raises(ValidationError, match=message):
        DeviceInput(name="Device", x=0.5, y=0.5, metadata=metadata)


def test_device_input_rejects_structured_metadata_values() -> None:
    with pytest.raises(TypeError, match="simple text"):
        DeviceInput(name="Device", x=0.5, y=0.5, metadata={"Owner": ["Ops"]})


def test_device_record_preserves_imported_structured_metadata() -> None:
    record = DeviceRecord(
        id="device-1",
        name="Imported",
        x=0.5,
        y=0.5,
        metadata={"Source": {"file": "sw_list.csv"}, "ports": [1, 2]},
        created_at="2026-09-30T12:00:00Z",
        updated_at="2026-09-30T12:00:00Z",
    )

    assert record.metadata["Source"] == {"file": "sw_list.csv"}
    assert record.metadata["ports"] == [1, 2]


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "", "x": 0, "y": 0},
        {"name": "Device", "x": -100.1, "y": 0},
        {"name": "Device", "x": 0, "y": 101.1},
        {"name": "Device", "x": 0, "y": 0, "node_type": "firewall"},
        {"name": "Device", "x": 0, "y": 0, "unexpected": True},
    ],
)
def test_device_input_rejects_invalid_fields(payload) -> None:
    with pytest.raises(ValidationError):
        DeviceInput(**payload)


def test_position_collections_require_unique_bounded_devices() -> None:
    with pytest.raises(ValidationError, match="only appear once"):
        DevicePositionsInput(
            positions=[
                DevicePosition(id="same", x=0, y=0),
                DevicePosition(id="same", x=1, y=1),
            ]
        )
    with pytest.raises(ValidationError):
        DevicePositionsInput(positions=[])
    with pytest.raises(ValidationError):
        DevicePosition(id="device", x=102, y=0)


def test_edge_and_pdf_models_reject_duplicate_or_invalid_selections() -> None:
    with pytest.raises(ValidationError, match="different nodes"):
        EdgeInput(source_id="same", target_id="same")
    with pytest.raises(ValidationError, match="only appear once"):
        TopologyPdfInput(node_ids=["a", "a"])
    with pytest.raises(ValidationError):
        TopologyPdfInput(node_ids=[], theme="sepia")


def test_mqtt_client_update_requires_a_change_and_strips_display_name() -> None:
    with pytest.raises(ValidationError, match="At least one"):
        MqttClientUpdateInput()
    with pytest.raises(ValidationError):
        MqttClientUpdateInput(display_name="x" * 101)

    assert MqttClientUpdateInput(display_name="  Branch office  ").display_name == "Branch office"
    assert MqttClientUpdateInput(state="approved").state == "approved"


def test_empty_liveness_request_forbids_client_supplied_targets() -> None:
    assert LivenessCheckInput().model_dump() == {}
    with pytest.raises(ValidationError):
        LivenessCheckInput(address="192.168.1.1")
