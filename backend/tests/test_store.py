"""Run store housekeeping: stale runs are deleted, live and recent ones kept."""

import asyncio
import os
import time

from app.runs import store

DAY = 24 * 3600


def _make_run(run_id: str, state: str, age_s: float, pid: int | None = None) -> None:
    """Create a run folder whose files were last written `age_s` seconds ago."""
    d = store.run_dir(run_id)
    (d / "baseline").mkdir(parents=True)
    store.write_json(d / "status.json", {"state": state, "pid": pid or 999_999_999})
    store.write_json(d / "result.json", {})
    old = time.time() - age_s
    for p in (d / "status.json", d / "result.json", d / "baseline", d):
        os.utime(p, (old, old))


def test_sweep_deletes_only_stale_finished_runs(data_dir):
    _make_run("aaaaaa000001", "done", 2 * DAY)              # stale -> deleted
    _make_run("aaaaaa000002", "error", 2 * DAY)             # stale -> deleted
    _make_run("aaaaaa000003", "running", 2 * DAY)           # worker gone -> deleted
    _make_run("aaaaaa000004", "done", 3600)                 # recent -> kept
    _make_run("aaaaaa000005", "running", 2 * DAY, os.getpid())  # worker alive -> kept
    (store.runs_root() / "NotARun").mkdir()                 # foreign folder -> kept

    assert store.sweep_stale_runs(DAY) == 3
    left = sorted(p.name for p in store.runs_root().iterdir())
    assert left == ["NotARun", "aaaaaa000004", "aaaaaa000005"]


def test_sweeper_loop_runs_at_start_up(data_dir):
    from app.main import sweep_runs_forever

    _make_run("bbbbbb000001", "done", 2 * DAY)

    async def one_sweep():
        task = asyncio.create_task(sweep_runs_forever(DAY, 3600))
        await asyncio.sleep(0.5)
        task.cancel()

    asyncio.run(one_sweep())
    assert not store.run_dir("bbbbbb000001").exists()
