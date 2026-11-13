"""HTTP endpoints.

    POST /api/chat                          conversation -> reply + RunRequest
    POST /api/llm/warm                      wake the LLM before the first chat
    POST /api/runs                          RunRequest -> {"id"}
    GET  /api/runs/{id}                     status, progress, request, result
    GET  /api/runs/{id}/network             GeoJSON road network
    GET  /api/runs/{id}/{variant}/meta      frame meta (origin, n_chunks, ...)
    GET  /api/runs/{id}/{variant}/chunks/{i} one quantised frame chunk

Runs execute in fast mode in the caller's own worker process (one per
session, keyed by the `X-Session-Id` header, else the client IP); the
browser replays the recorded frames afterwards.
"""

import re
import shutil
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from fastapi.responses import FileResponse

from app.api.schemas import ChatReply, ChatRequest, RunRequest
from app.llm import agent
from app.llm.agent import respond
from app.runs import store, worker
from app.runs.export import chunk_json

router = APIRouter(prefix="/api")
_SESSION = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def session_key(request: Request, x_session_id: str | None) -> str:
    """Worker key of the caller: the session header, else the client address.

    Raises:
        HTTPException 400: if the header is present but malformed.
    """
    if x_session_id is not None:
        if not _SESSION.match(x_session_id):
            raise HTTPException(400, "X-Session-Id must be 8-64 chars of [A-Za-z0-9_-]")
        return f"s:{x_session_id}"
    return f"ip:{request.client.host if request.client else 'unknown'}"


def _dir(run_id: str) -> Path:
    """Existing run folder or 404."""
    try:
        d = store.run_dir(run_id)
    except ValueError as exc:
        raise HTTPException(400, "bad run id") from exc
    if not d.exists():
        raise HTTPException(404, "run not found")
    return d


@router.get("/health")
def health() -> dict:
    """Liveness probe."""
    return {"ok": True}


@router.post("/chat", response_model=ChatReply)
def chat(body: ChatRequest) -> ChatReply:
    """Task-understanding agent turn (Ollama LLM, rule-based fallback)."""
    return respond(body.messages, body.params)


@router.post("/llm/warm", status_code=202)
def warm_llm(background: BackgroundTasks) -> dict:
    """Start the LLM in the background (called when the page opens).

    Returns at once; `warming` is false when a warm-up was sent recently.
    """
    due = agent.claim_warm()
    if due:
        background.add_task(agent.warm)
    return {"warming": due}


@router.post("/runs", status_code=202)
def create_run(req: RunRequest, request: Request,
               x_session_id: str | None = Header(default=None)) -> dict:
    """Validate, persist and queue a run on the caller's worker.

    Returns:
        {"id": run_id}. 503 with Retry-After when every worker is busy.
    """
    session = session_key(request, x_session_id)
    run_id = store.new_run_id()
    d = store.run_dir(run_id)
    d.mkdir(parents=True)
    store.write_json(d / "request.json", req.model_dump())
    store.set_status(run_id, "queued")
    try:
        worker.submit(session, run_id, req.model_dump())
    except worker.CapacityError as exc:
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(503, f"{exc}; retry shortly",
                            headers={"Retry-After": str(worker.RETRY_AFTER_S)}) from exc
    return {"id": run_id}


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    """Status, progress and (when done) the full result of a run."""
    d = _dir(run_id)
    request = store.read_json(d / "request.json")
    status = store.read_json(d / "status.json") or {"state": "queued"}
    return {"id": run_id, **status, "request": request,
            "progress": 1.0 if status["state"] == "done" else store.progress(run_id, request),
            "has_network": (d / "network.json").exists(),
            "result": store.read_json(d / "result.json")}


@router.get("/runs/{run_id}/network")
def get_network(run_id: str) -> FileResponse:
    """GeoJSON of the simulated road network."""
    f = _dir(run_id) / "network.json"
    if not f.exists():
        raise HTTPException(404, "network not ready")
    return FileResponse(f, media_type="application/json")


@router.get("/runs/{run_id}/{variant}/meta")
def get_meta(run_id: str, variant: str) -> FileResponse:
    """Frame metadata of one variant."""
    if variant not in store.VARIANTS:
        raise HTTPException(404, "unknown variant")
    f = _dir(run_id) / variant / "meta.json"
    if not f.exists():
        raise HTTPException(404, "frames not ready")
    return FileResponse(f, media_type="application/json")


@router.get("/runs/{run_id}/{variant}/chunks/{index}")
def get_chunk(run_id: str, variant: str, index: int) -> dict:
    """One chunk of recorded frames, quantised for the browser."""
    if variant not in store.VARIANTS or not 0 <= index < 100_000:
        raise HTTPException(404, "unknown chunk")
    f = _dir(run_id) / variant / f"chunk_{index:05d}.npz"
    if not f.exists():
        raise HTTPException(404, "chunk not ready")
    return chunk_json(f)
