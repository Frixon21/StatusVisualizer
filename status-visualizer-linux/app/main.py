from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import Settings, resource_path
from app.database import Repository
from app.lantopolog import ExportValidationError, parse_lantopolog_export
from app.liveness import LivenessProbe, LivenessService
from app.models import (
    DeviceInput,
    DevicePositionsInput,
    EdgeInput,
    LantopologImportInput,
    LivenessCheckInput,
    TopologyPdfInput,
    utc_now,
)
from app.topology_pdf import render_topology_pdf


def create_app(
    settings: Settings | None = None,
    *,
    liveness_probe: LivenessProbe | None = None,
) -> FastAPI:
    application_settings = settings or Settings()
    repository = Repository(application_settings.database_path)
    liveness_service = LivenessService(
        repository,
        probe=liveness_probe,
        max_concurrency=application_settings.liveness_max_concurrency,
        timeout_seconds=application_settings.liveness_timeout_seconds,
        interval_seconds=application_settings.liveness_interval_seconds,
    )
    pdf_export_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await asyncio.to_thread(repository.initialize)
        stop_event = asyncio.Event()
        liveness_task = asyncio.create_task(
            liveness_service.run(stop_event), name="liveness-checks"
        )
        try:
            yield
        finally:
            stop_event.set()
            liveness_task.cancel()
            await asyncio.gather(liveness_task, return_exceptions=True)

    app = FastAPI(
        title="Lantopolog Topology Visualizer",
        version=__version__,
        docs_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = application_settings
    app.state.repository = repository
    app.state.liveness_service = liveness_service

    @app.middleware("http")
    async def limit_request_size(request: Request, call_next):
        if request.method == "POST" and request.url.path == "/api/liveness/check":
            if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                return Response("JSON request required", status_code=415)
            if request.headers.get("x-status-visualizer-request") != "1":
                return Response("Status Visualizer request header required", status_code=403)
            liveness_length = request.headers.get("content-length")
            if liveness_length is None:
                return Response("Content-Length required", status_code=411)
            try:
                if int(liveness_length) > 128:
                    return Response("Request payload is too large", status_code=413)
            except ValueError:
                return Response("Invalid Content-Length", status_code=400)
            origin = request.headers.get("origin")
            expected_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
            if origin and origin.rstrip("/").lower() != expected_origin.rstrip("/").lower():
                return Response("Cross-origin liveness checks are not allowed", status_code=403)
        if request.method == "POST" and request.url.path == "/api/exports/topology.pdf":
            if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                return Response("JSON request required", status_code=415)
            if request.headers.get("x-status-visualizer-request") != "1":
                return Response("Status Visualizer request header required", status_code=403)
            origin = request.headers.get("origin")
            expected_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
            if origin and origin.rstrip("/").lower() != expected_origin.rstrip("/").lower():
                return Response("Cross-origin PDF exports are not allowed", status_code=403)
        if request.method in {"POST", "PUT", "PATCH"}:
            maximum_bytes = (
                21 * 1024 * 1024
                if request.url.path == "/api/import/lantopolog"
                else 1024 * 1024
            )
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > maximum_bytes:
                        return Response("Request payload is too large", status_code=413)
                except ValueError:
                    return Response("Invalid Content-Length", status_code=400)
        return await call_next(request)

    static_dir = resource_path("app", "static")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/topology")
    async def topology() -> dict:
        snapshot = await asyncio.to_thread(repository.topology_snapshot)
        return {
            "generated_at": utc_now(),
            "nodes": [node.model_dump(mode="json") for node in snapshot["nodes"]],
            "edges": [edge.model_dump(mode="json") for edge in snapshot["edges"]],
            "interfaces": snapshot["interfaces"],
            "vlans": snapshot["vlans"],
            "import_status": snapshot["import_status"],
        }

    @app.post("/api/exports/topology.pdf")
    async def export_topology_pdf(payload: TopologyPdfInput) -> Response:
        if pdf_export_lock.locked():
            raise HTTPException(status_code=409, detail="A PDF export is already running")
        async with pdf_export_lock:
            snapshot = await asyncio.to_thread(repository.topology_snapshot)
            try:
                pdf = await asyncio.to_thread(
                    render_topology_pdf,
                    snapshot,
                    payload.node_ids,
                    label_mode=payload.label_mode,
                    show_vlans=payload.vlan_view,
                    theme=payload.theme,
                )
            except ValueError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error
        exported_at = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": (
                    f'attachment; filename="status-visualizer-topology-{exported_at}.pdf"'
                ),
                "X-Content-Type-Options": "nosniff",
                "X-Topology-Node-Count": str(len(payload.node_ids)),
                "X-Topology-Theme": payload.theme,
            },
        )

    def liveness_payload() -> dict:
        devices = repository.list_liveness_statuses()
        return {
            "generated_at": utc_now(),
            "summary": {
                "devices": len(devices),
                "online": sum(item.state == "online" for item in devices),
                "offline": sum(item.state == "offline" for item in devices),
                "unknown": sum(item.state == "unknown" for item in devices),
            },
            "devices": [item.model_dump(mode="json") for item in devices],
        }

    @app.get("/api/liveness")
    async def current_liveness() -> dict:
        return await asyncio.to_thread(liveness_payload)

    @app.post("/api/liveness/check")
    async def check_liveness(_: LivenessCheckInput) -> dict:
        if liveness_service.is_checking:
            raise HTTPException(status_code=409, detail="A liveness check is already running")
        summary = await liveness_service.check_now()
        payload = await asyncio.to_thread(liveness_payload)
        payload["check"] = asdict(summary)
        return payload

    @app.post("/api/import/lantopolog")
    async def import_lantopolog(payload: LantopologImportInput) -> dict:
        files = {item.path.replace("\\", "/"): item.content for item in payload.files}
        try:
            parsed = await asyncio.to_thread(parse_lantopolog_export, files)
            summary = await asyncio.to_thread(repository.replace_lantopolog, parsed)
        except ExportValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except (UnicodeError, ValueError) as error:
            raise HTTPException(status_code=422, detail="The export contains invalid data") from error
        return {"imported": True, "summary": summary}

    @app.get("/api/devices")
    async def list_devices() -> list[dict]:
        return [item.model_dump(mode="json") for item in await asyncio.to_thread(repository.list_devices)]

    @app.put("/api/devices/positions")
    async def update_device_positions(payload: DevicePositionsInput) -> list[dict]:
        try:
            devices = await asyncio.to_thread(repository.update_device_positions, payload.positions)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return [device.model_dump(mode="json") for device in devices]

    @app.get("/api/layouts/custom")
    async def get_custom_layout() -> dict:
        return await asyncio.to_thread(repository.get_custom_layout)

    @app.put("/api/layouts/custom")
    async def save_custom_layout(payload: DevicePositionsInput) -> dict:
        return await asyncio.to_thread(repository.save_custom_layout, payload.positions)

    @app.get("/api/devices/{device_id}")
    async def get_device(device_id: str) -> dict:
        device = await asyncio.to_thread(repository.get_device, device_id)
        if device is None:
            raise HTTPException(status_code=404, detail="Device not found")
        return device.model_dump(mode="json")

    @app.post("/api/devices", status_code=status.HTTP_201_CREATED)
    async def create_device(payload: DeviceInput) -> dict:
        return (await asyncio.to_thread(repository.create_device, payload)).model_dump(mode="json")

    @app.delete("/api/devices")
    async def delete_all_devices() -> dict:
        deleted = await asyncio.to_thread(repository.delete_all_devices)
        return {"deleted": deleted}

    @app.put("/api/devices/{device_id}")
    async def update_device(device_id: str, payload: DeviceInput) -> dict:
        device = await asyncio.to_thread(repository.update_device, device_id, payload)
        if device is None:
            raise HTTPException(status_code=404, detail="Device not found")
        return device.model_dump(mode="json")

    @app.delete("/api/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_device(device_id: str) -> Response:
        if not await asyncio.to_thread(repository.delete_device, device_id):
            raise HTTPException(status_code=404, detail="Device not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/edges")
    async def list_edges() -> list[dict]:
        return [edge.model_dump(mode="json") for edge in await asyncio.to_thread(repository.list_edges)]

    @app.post("/api/edges", status_code=status.HTTP_201_CREATED)
    async def create_edge(payload: EdgeInput) -> dict:
        try:
            edge = await asyncio.to_thread(repository.create_edge, payload)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return edge.model_dump(mode="json")

    @app.delete("/api/edges/{edge_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_edge(edge_id: str) -> Response:
        if not await asyncio.to_thread(repository.delete_edge, edge_id):
            raise HTTPException(status_code=404, detail="Connection not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return app


app = create_app()
