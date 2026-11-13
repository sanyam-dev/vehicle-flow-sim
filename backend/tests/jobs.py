"""Tiny picklable job functions for WorkerManager tests (run in spawned workers)."""

import os
import time
from pathlib import Path


def record(run_id: str, request: dict) -> None:
    """Append "<run_id> <pid>" to request["log"], after an optional sleep.

    Args:
        run_id: Job id.
        request: {"log": file path, "sleep": seconds (optional)}.
    """
    time.sleep(request.get("sleep", 0.0))
    with Path(request["log"]).open("a") as fh:
        fh.write(f"{run_id} {os.getpid()}\n")
