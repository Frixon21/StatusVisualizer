from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.heatmap_bands import DEFAULT_BANDS, js_path, json_path, save_bands, validate_bands
from app.main import create_app


def test_validate_bands_rejects_bad_order(tmp_path: Path) -> None:
    bad = list(DEFAULT_BANDS)
    bad[0], bad[1] = bad[1], bad[0]
    with pytest.raises(Exception):
        validate_bands(bad)


def test_save_bands_writes_json_and_js(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    static = tmp_path / "app" / "static"
    static.mkdir(parents=True)
    monkeypatch.setattr("app.heatmap_bands.resource_path", lambda *parts: tmp_path.joinpath(*parts))

    saved = save_bands(DEFAULT_BANDS)
    assert saved == validate_bands(DEFAULT_BANDS)
    document = json.loads(json_path().read_text(encoding="utf-8"))
    assert document[3]["level"] == "warning"
    assert "globalThis.__HEATMAP_LOSS_BANDS" in js_path().read_text(encoding="utf-8")


def test_dev_heatmap_api_localhost_only(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        assert client.get("/dev/heatmap").status_code == 200
        bands = client.get("/api/dev/heatmap-bands").json()
        assert len(bands) == 7
        assert bands[0]["level"] == "healthy"


def test_dev_heatmap_reset(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        mutated = list(DEFAULT_BANDS)
        mutated[0] = {**mutated[0], "opacity": 0.05}
        client.put(
            "/api/dev/heatmap-bands",
            json=mutated,
            headers={"X-Status-Visualizer-Request": "1"},
        ).raise_for_status()
        reset = client.post(
            "/api/dev/heatmap-bands/reset",
            headers={
                "Content-Type": "application/json",
                "X-Status-Visualizer-Request": "1",
            },
            json={},
        )
        reset.raise_for_status()
        assert reset.json()[0]["opacity"] == DEFAULT_BANDS[0]["opacity"]
