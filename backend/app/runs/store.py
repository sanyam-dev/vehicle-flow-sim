"""File-based run store.

Layout under `<data_dir>/runs/<run_id>/`:

    request.json      validated RunRequest
    status.json       {"state": queued|running|done|error, "stage", "error"}
    network.json      GeoJSON of the baseline network (+ scenario edits flagged)
    result.json       run_pair() output (KPIs, series, comparison)
    baseline/         recorder chunks + meta.json
    scenario/         same, when the request has graph edits

Files are the source of truth so status survives API restarts and the
worker process needs no channel back to the API.
"""

import json
import multiprocessing
import os
import re
import secrets
import time
from pathlib import Path

from app.config import get_settings

_RUN_ID = re.compile(r"^[a-z0-9]{6,32}$")
VARIANTS = ("baseline", "scenario")


def runs_root() -> Path:
    """Folder holding every run."""
    root = Path(get_settings().data_dir) / "runs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def new_run_id() -> str:
    """Random, URL-safe run id."""
    return secrets.token_hex(6)


def run_dir(run_id: str) -> Path:
    """Folder of one run.

    Raises:
        ValueError: if the id is malformed (prevents path traversal).
    """
    if not _RUN_ID.match(run_id):
        raise ValueError("bad run id")
    return runs_root() / run_id


def write_json(path: Path, obj) -> None:
    """Atomically write JSON (write temp, then rename)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":")))
    tmp.replace(path)


def read_json(path: Path):
    """Read JSON or return None if the file does not exist."""
    return json.loads(path.read_text()) if path.exists() else None


def set_status(run_id: str, state: str, stage: str = "", error: str = "") -> None:
    """Persist the run's lifecycle state.

    Also records the writing process id (`pid`), so the API can tell a run
    whose worker died from one still running, and the time (`updated`).
    """
    write_json(run_dir(run_id) / "status.json",
               {"state": state, "stage": stage, "error": error,
                "pid": os.getpid(), "updated": time.time()})


def _alive(pid: int) -> bool:
    """True if a process with this id exists (signal 0 only checks).

    Workers are children of the API process: a dead one stays a zombie (and
    still answers signal 0) until reaped, so reap finished children first.
    """
    multiprocessing.active_children()
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # exists, owned by another user
        return True
    return True


def reconcile_dead(run_id: str, status: dict) -> dict:
    """Turn a "running" run whose worker process has died into an error.

    Args:
        run_id: Run id.
        status: The run's status.json content.
    Returns:
        The status to report (rewritten on disk when the worker is gone).
    """
    pid = status.get("pid")
    if status.get("state") != "running" or not isinstance(pid, int) or _alive(pid):
        return status
    msg = "The simulation worker stopped unexpectedly; please start the run again."
    write_json(run_dir(run_id) / "status.json",
               {**status, "state": "error", "error": msg, "updated": time.time()})
    return read_json(run_dir(run_id) / "status.json")


def progress(run_id: str, request: dict) -> float:
    """Fraction of simulated time done, estimated from written frame chunks.

    Args:
        run_id: Run id.
        request: The run's RunRequest as a dict.
    Returns:
        0..1 progress over all variants of the run.
    """
    from app.api.schemas import SCENARIO_FIELDS
    from app.sim.recorder import CHUNK_FRAMES

    d = run_dir(run_id)
    n_var = 2 if any(request.get(k) for k in SCENARIO_FIELDS) else 1
    total = n_var * max(request["duration_s"] / CHUNK_FRAMES, 1.0)
    done = sum(len(list((d / v).glob("chunk_*.npz"))) for v in VARIANTS if (d / v).exists())
    return min(done / total, 1.0)
