"""FastAPI application entry point (`uvicorn app.main:app`)."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api.routes import router
from app.config import get_settings
from app.runs import worker


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Shut the worker pool down with the API."""
    yield
    worker.shutdown()


def create_app() -> FastAPI:
    """Build the app with CORS for the configured frontend origins."""
    app = FastAPI(title="Vehicle Flow Simulator", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origin_list,
                       allow_origin_regex=r"https://.*\.vercel\.app",
                       allow_methods=["GET", "POST"], allow_headers=["*"])
    app.add_middleware(GZipMiddleware, minimum_size=2048)
    app.include_router(router)
    return app


app = create_app()
