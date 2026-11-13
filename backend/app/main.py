"""FastAPI application entry point (`uvicorn app.main:app`).

Serves the JSON API under /api and, when the built frontend exists
(`static_dir`, i.e. frontend/dist), the web app itself at /. Frontend and
API then share one origin, so the browser needs no CORS in production.
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from app.api.routes import router
from app.config import get_settings
from app.geo.seed import install_seed
from app.runs import worker


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Install shipped areas on start-up; shut the workers down with the API."""
    install_seed()
    yield
    worker.shutdown()


class FrontendFiles(StaticFiles):
    """Built frontend; hashed /assets/* files are cached for a year."""

    async def get_response(self, path: str, scope: Scope):
        """Serve a file, adding long-lived caching for content-hashed assets."""
        resp = await super().get_response(path, scope)
        if path.startswith("assets/") and resp.status_code == 200:
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp


def create_app() -> FastAPI:
    """Build the app: API routes, CORS for dev origins, and the built frontend if present."""
    s = get_settings()
    app = FastAPI(title="Vehicle Flow Simulator", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origin_list,
                       allow_methods=["GET", "POST"], allow_headers=["*"])
    app.add_middleware(GZipMiddleware, minimum_size=2048)
    app.include_router(router)
    if (Path(s.static_dir) / "index.html").exists():
        app.mount("/", FrontendFiles(directory=s.static_dir, html=True), name="frontend")
    return app


app = create_app()
