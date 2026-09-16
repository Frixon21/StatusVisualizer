"""Small, dependency-light web app for the Tech Expo network chooser."""

import argparse

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles


ASSET_DIR = Path(__file__).resolve().parent / "expo-launcher"

app = FastAPI(
    title="Status Visualizer Expo Launcher",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next) -> Response:
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'; object-src 'none'"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "status-visualizer-launcher"}


@app.get("/", response_class=FileResponse)
def index() -> FileResponse:
    return FileResponse(ASSET_DIR / "index.html")


app.mount("/assets", StaticFiles(directory=ASSET_DIR), name="assets")


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the Status Visualizer expo launcher")
    parser.add_argument("--host", default="127.0.0.1", help="Address to listen on")
    parser.add_argument("--port", type=int, default=6042, help="TCP port to listen on")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
