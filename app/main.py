from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import Settings, resource_path
from app.heatmap_bands import DEFAULT_BANDS, HeatmapBandsError, ensure_generated_files, load_bands, save_bands
from app.database import Repository
from app.lantopolog import ExportValidationError, parse_lantopolog_export
from app.liveness import LivenessProbe, LivenessService
from app.models import (
    DeviceInput,
    DevicePositionsInput,
    EdgeInput,
    LantopologImportInput,
    LivenessCheckInput,
    MqttClientUpdateInput,
    TopologyPdfInput,
    utc_now,
)
from app.mqtt_config import MqttConfigError, load_mqtt_config, resolve_mqtt_config_path
from app.mqtt_service import MqttSnapshotSubscriber
from app.remote_status import RemoteStatusStore, StatusValidationError
from app.site_registry import LOCAL_SITE_ID, SiteRegistry, SiteRegistryError
from app.topology_pdf import render_topology_pdf

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    liveness_probe: LivenessProbe | None = None,
) -> FastAPI:
    application_settings = settings or Settings()
    site_registry = SiteRegistry(application_settings.database_path.parent)
    repository = site_registry.repository_for(LOCAL_SITE_ID)
    liveness_service = LivenessService(
        repository,
        probe=liveness_probe,
        max_concurrency=application_settings.liveness_max_concurrency,
        timeout_seconds=application_settings.liveness_timeout_seconds,
        interval_seconds=application_settings.liveness_interval_seconds,
    )
    pdf_export_lock = asyncio.Lock()
    mqtt_subscriber: MqttSnapshotSubscriber | None = None
    mqtt_queue: asyncio.Queue[tuple[str, str, bytes]] = asyncio.Queue(maxsize=32)
    remote_status_store = RemoteStatusStore()

    def selected_site(request: Request) -> str:
        return request.headers.get("x-status-visualizer-site", "local").strip() or "local"

    def selected_repository(request: Request) -> Repository:
        try:
            return site_registry.repository_for(selected_site(request))
        except SiteRegistryError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    async def consume_mqtt() -> None:
        while True:
            message_type, client_id, payload = await mqtt_queue.get()
            try:
                if message_type == "snapshot":
                    await asyncio.to_thread(site_registry.ingest, client_id, payload)
                else:
                    await asyncio.to_thread(site_registry.repository_for, client_id)
                    await asyncio.to_thread(remote_status_store.ingest, client_id, payload)
            except (SiteRegistryError, StatusValidationError) as error:
                logger.warning("Rejected MQTT %s for client %s: %s", message_type, client_id, error)
            except Exception:
                logger.exception("Could not process MQTT %s for client %s", message_type, client_id)
            finally:
                mqtt_queue.task_done()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        nonlocal mqtt_subscriber
        ensure_generated_files()
        stop_event = asyncio.Event()
        liveness_task = asyncio.create_task(
            liveness_service.run(stop_event), name="liveness-checks"
        )
        mqtt_task = asyncio.create_task(consume_mqtt(), name="mqtt-ingestion")
        config_path = resolve_mqtt_config_path()
        if config_path:
            try:
                mqtt_config = load_mqtt_config(config_path)
                loop = asyncio.get_running_loop()

                def enqueue_mqtt(message_type: str, item: tuple[str, bytes]) -> None:
                    def deliver() -> None:
                        try:
                            mqtt_queue.put_nowait((message_type, *item))
                        except asyncio.QueueFull:
                            logger.warning("MQTT ingestion queue is full; retained delivery will retry after reconnect")

                    loop.call_soon_threadsafe(deliver)

                mqtt_subscriber = MqttSnapshotSubscriber(
                    mqtt_config,
                    lambda item: enqueue_mqtt("snapshot", item),
                    on_status=lambda item: enqueue_mqtt("status", item),
                )
                mqtt_subscriber.start()
                logger.info("MQTT subscriber enabled using %s", config_path)
            except (MqttConfigError, OSError, ImportError) as error:
                logger.error("MQTT subscriber is disabled: %s", error)
        try:
            yield
        finally:
            if mqtt_subscriber is not None:
                mqtt_subscriber.stop()
            stop_event.set()
            liveness_task.cancel()
            mqtt_task.cancel()
            await asyncio.gather(liveness_task, mqtt_task, return_exceptions=True)

    app = FastAPI(
        title="Lantopolog Topology Visualizer",
        version=__version__,
        docs_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = application_settings
    app.state.site_registry = site_registry
    app.state.remote_status_store = remote_status_store
    app.state.liveness_service = liveness_service

    def trusted_json_request(request: Request, origin_error: str) -> Response | None:
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            return Response("JSON request required", status_code=415)
        if request.headers.get("x-status-visualizer-request") != "1":
            return Response("Status Visualizer request header required", status_code=403)
        origin = request.headers.get("origin")
        expected_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if origin and origin.rstrip("/").lower() != expected_origin.rstrip("/").lower():
            return Response(origin_error, status_code=403)
        return None

    def enforce_content_length(
        request: Request,
        maximum_bytes: int,
        *,
        required: bool = False,
    ) -> Response | None:
        content_length = request.headers.get("content-length")
        if content_length is None:
            return Response("Content-Length required", status_code=411) if required else None
        try:
            if int(content_length) > maximum_bytes:
                return Response("Request payload is too large", status_code=413)
        except ValueError:
            return Response("Invalid Content-Length", status_code=400)
        return None

    @app.middleware("http")
    async def limit_request_size(request: Request, call_next):
        guard_error: Response | None = None
        if request.method == "PATCH" and request.url.path.startswith("/api/mqtt/clients/"):
            guard_error = trusted_json_request(
                request,
                "Cross-origin client changes are not allowed",
            )
        elif request.method == "POST" and request.url.path == "/api/liveness/check":
            guard_error = trusted_json_request(
                request,
                "Cross-origin liveness checks are not allowed",
            )
            if guard_error is None:
                guard_error = enforce_content_length(request, 128, required=True)
        elif request.method == "POST" and request.url.path == "/api/exports/topology.pdf":
            guard_error = trusted_json_request(
                request,
                "Cross-origin PDF exports are not allowed",
            )
        if guard_error is not None:
            return guard_error
        if request.method in {"POST", "PUT", "PATCH"}:
            maximum_bytes = (
                21 * 1024 * 1024
                if request.url.path == "/api/import/lantopolog"
                else 1024 * 1024
            )
            if length_error := enforce_content_length(request, maximum_bytes):
                return length_error
        return await call_next(request)

    static_dir = resource_path("app", "static")
    heatmap_dev_path = static_dir / "heatmap-dev.html"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/api/health")
    async def health() -> dict[str, str | bool]:
        return {
            "status": "ok",
            "version": __version__,
            "heatmap_dev_tools": heatmap_dev_path.is_file(),
        }

    @app.get("/api/sites")
    async def sites() -> list[dict]:
        return [
            {
                "id": site.site_id,
                "display_name": site.display_name,
                "kind": "local" if site.is_local else "mqtt",
                "last_imported_at": site.last_imported_at,
                "last_snapshot_hash": site.last_snapshot_hash,
            }
            for site in await asyncio.to_thread(site_registry.list_sites)
        ]

    @app.get("/api/mqtt/clients")
    async def mqtt_clients() -> dict:
        clients = await asyncio.to_thread(site_registry.list_clients)
        return {
            "clients": [
                {
                    "client_id": client.client_id,
                    "display_name": client.display_name,
                    "state": client.state.value,
                    "first_seen_at": client.first_seen_at,
                    "last_seen_at": client.last_seen_at,
                    "last_snapshot_hash": client.last_snapshot_hash,
                    "last_imported_at": client.last_imported_at,
                    "last_error": client.last_error,
                }
                for client in clients
            ],
            "mqtt": {
                "configured": mqtt_subscriber is not None,
                "connected": bool(mqtt_subscriber and mqtt_subscriber.connected),
            },
        }

    @app.patch("/api/mqtt/clients/{client_id}")
    async def update_mqtt_client(client_id: str, payload: MqttClientUpdateInput) -> dict:
        try:
            client = await asyncio.to_thread(
                site_registry.patch_client,
                client_id,
                display_name=payload.display_name,
                state=payload.state,
            )
        except SiteRegistryError as error:
            status_code = 404 if "not found" in str(error).lower() else 422
            raise HTTPException(status_code=status_code, detail=str(error)) from error
        return {
            "client_id": client.client_id,
            "display_name": client.display_name,
            "state": client.state.value,
            "first_seen_at": client.first_seen_at,
            "last_seen_at": client.last_seen_at,
            "last_snapshot_hash": client.last_snapshot_hash,
            "last_imported_at": client.last_imported_at,
            "last_error": client.last_error,
        }

    @app.get("/api/topology")
    async def topology(request: Request) -> dict:
        snapshot = await asyncio.to_thread(selected_repository(request).topology_snapshot)
        return {
            "generated_at": utc_now(),
            "nodes": [node.model_dump(mode="json") for node in snapshot["nodes"]],
            "edges": [edge.model_dump(mode="json") for edge in snapshot["edges"]],
            "interfaces": snapshot["interfaces"],
            "vlans": snapshot["vlans"],
            "import_status": snapshot["import_status"],
        }

    @app.post("/api/exports/topology.pdf")
    async def export_topology_pdf(payload: TopologyPdfInput, request: Request) -> Response:
        if pdf_export_lock.locked():
            raise HTTPException(status_code=409, detail="A PDF export is already running")
        async with pdf_export_lock:
            snapshot = await asyncio.to_thread(selected_repository(request).topology_snapshot)
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

    def liveness_payload(selected: Repository, *, available: bool = True) -> dict:
        devices = selected.list_liveness_statuses()
        return {
            "generated_at": utc_now(),
            "available": available,
            "summary": {
                "devices": len(devices),
                "online": sum(item.state == "online" for item in devices),
                "offline": sum(item.state == "offline" for item in devices),
                "unknown": sum(item.state == "unknown" for item in devices),
            },
            "devices": [item.model_dump(mode="json") for item in devices],
        }

    @app.get("/api/liveness")
    async def current_liveness(request: Request) -> dict:
        site_id = selected_site(request)
        selected = selected_repository(request)
        if site_id == "local":
            return await asyncio.to_thread(liveness_payload, selected)
        return await asyncio.to_thread(remote_status_store.liveness_payload, site_id, selected)

    @app.post("/api/liveness/check")
    async def check_liveness(_: LivenessCheckInput, request: Request) -> dict:
        if selected_site(request) != "local":
            selected_repository(request)
            raise HTTPException(status_code=409, detail="Central ping is unavailable for remote MQTT sites")
        if liveness_service.is_checking:
            raise HTTPException(status_code=409, detail="A liveness check is already running")
        summary = await liveness_service.check_now()
        payload = await asyncio.to_thread(liveness_payload, repository)
        payload["check"] = asdict(summary)
        return payload

    @app.post("/api/import/lantopolog")
    async def import_lantopolog(payload: LantopologImportInput, request: Request) -> dict:
        files = {item.path.replace("\\", "/"): item.content for item in payload.files}
        selected = selected_repository(request)
        try:
            parsed = await asyncio.to_thread(parse_lantopolog_export, files)
            summary = await asyncio.to_thread(selected.replace_lantopolog, parsed)
        except ExportValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except (UnicodeError, ValueError) as error:
            raise HTTPException(status_code=422, detail="The export contains invalid data") from error
        return {"imported": True, "summary": summary}

    @app.get("/api/devices")
    async def list_devices(request: Request) -> list[dict]:
        return [item.model_dump(mode="json") for item in await asyncio.to_thread(selected_repository(request).list_devices)]

    @app.put("/api/devices/positions")
    async def update_device_positions(payload: DevicePositionsInput, request: Request) -> list[dict]:
        try:
            devices = await asyncio.to_thread(selected_repository(request).update_device_positions, payload.positions)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return [device.model_dump(mode="json") for device in devices]

    @app.get("/api/layouts/custom")
    async def get_custom_layout(request: Request) -> dict:
        return await asyncio.to_thread(selected_repository(request).get_custom_layout)

    @app.put("/api/layouts/custom")
    async def save_custom_layout(payload: DevicePositionsInput, request: Request) -> dict:
        return await asyncio.to_thread(selected_repository(request).save_custom_layout, payload.positions)

    @app.get("/api/devices/{device_id}")
    async def get_device(device_id: str, request: Request) -> dict:
        device = await asyncio.to_thread(selected_repository(request).get_device, device_id)
        if device is None:
            raise HTTPException(status_code=404, detail="Device not found")
        return device.model_dump(mode="json")

    @app.post("/api/devices", status_code=status.HTTP_201_CREATED)
    async def create_device(payload: DeviceInput, request: Request) -> dict:
        return (await asyncio.to_thread(selected_repository(request).create_device, payload)).model_dump(mode="json")

    @app.delete("/api/devices")
    async def delete_all_devices(request: Request) -> dict:
        deleted = await asyncio.to_thread(selected_repository(request).delete_all_devices)
        return {"deleted": deleted}

    @app.put("/api/devices/{device_id}")
    async def update_device(device_id: str, payload: DeviceInput, request: Request) -> dict:
        device = await asyncio.to_thread(selected_repository(request).update_device, device_id, payload)
        if device is None:
            raise HTTPException(status_code=404, detail="Device not found")
        return device.model_dump(mode="json")

    @app.delete("/api/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_device(device_id: str, request: Request) -> Response:
        if not await asyncio.to_thread(selected_repository(request).delete_device, device_id):
            raise HTTPException(status_code=404, detail="Device not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/edges")
    async def list_edges(request: Request) -> list[dict]:
        return [edge.model_dump(mode="json") for edge in await asyncio.to_thread(selected_repository(request).list_edges)]

    @app.post("/api/edges", status_code=status.HTTP_201_CREATED)
    async def create_edge(payload: EdgeInput, request: Request) -> dict:
        try:
            edge = await asyncio.to_thread(selected_repository(request).create_edge, payload)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return edge.model_dump(mode="json")

    @app.delete("/api/edges/{edge_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_edge(edge_id: str, request: Request) -> Response:
        if not await asyncio.to_thread(selected_repository(request).delete_edge, edge_id):
            raise HTTPException(status_code=404, detail="Connection not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    def dev_tools_allowed(request: Request) -> bool:
        if not heatmap_dev_path.is_file():
            return False
        client = request.client
        return client is not None and client.host in {"127.0.0.1", "::1", "testclient"}

    @app.get("/dev/heatmap", include_in_schema=False)
    async def heatmap_dev_page() -> FileResponse:
        if not heatmap_dev_path.is_file():
            raise HTTPException(
                status_code=404,
                detail=(
                    "Heatmap tuner is not available in this build. "
                    "Stop StatusVisualizer.exe if it is running on this port, then from the project folder run "
                    " .\\.venv\\Scripts\\python.exe run.py and open http://127.0.0.1:8092/dev/heatmap"
                ),
            )
        return FileResponse(heatmap_dev_path)

    if heatmap_dev_path.is_file():
        @app.get("/api/dev/heatmap-bands", include_in_schema=False)
        async def read_heatmap_bands(request: Request) -> list[dict]:
            if not dev_tools_allowed(request):
                raise HTTPException(status_code=404, detail="Not found")
            return await asyncio.to_thread(load_bands)

        @app.put("/api/dev/heatmap-bands", include_in_schema=False, response_model=None)
        async def write_heatmap_bands(payload: list[dict], request: Request) -> list[dict] | Response:
            if not dev_tools_allowed(request):
                raise HTTPException(status_code=404, detail="Not found")
            guard = trusted_json_request(request, "Cross-origin heatmap saves are not allowed")
            if guard is not None:
                return guard
            try:
                return await asyncio.to_thread(save_bands, payload)
            except HeatmapBandsError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error

        @app.post("/api/dev/heatmap-bands/reset", include_in_schema=False, response_model=None)
        async def reset_heatmap_bands(request: Request) -> list[dict] | Response:
            if not dev_tools_allowed(request):
                raise HTTPException(status_code=404, detail="Not found")
            guard = trusted_json_request(request, "Cross-origin heatmap saves are not allowed")
            if guard is not None:
                return guard
            return await asyncio.to_thread(save_bands, DEFAULT_BANDS)

    return app


app = create_app()
