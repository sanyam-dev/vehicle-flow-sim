"""Simulation jobs, one dedicated worker process per user session.

`WorkerManager` keeps one spawned process per session id and feeds it
jobs through a queue, so one user's heavy run never blocks the API or
another user's runs. Rules:

* a session's runs execute one after another in its own process;
* an idle worker (no unfinished jobs for `worker_idle_timeout_s`) is told
  to exit with a sentinel, which is queued behind any jobs, so no job is lost;
* at most `max_workers` processes exist; when full, an idle worker is
  evicted, and if every worker is busy `submit` raises CapacityError
  (the API answers 503 + Retry-After).

`execute()` is the job run inside the worker: geocode, load the cached
graph, apply scenario edits (`scenario.builder`), run baseline + scenario in
fast mode, and write everything to the run store.
"""

import multiprocessing as mp
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field

from app.config import get_settings

RETRY_AFTER_S = 30


class CapacityError(RuntimeError):
    """Every worker slot is busy; the caller should retry later."""


def _serve(jobs, finished, target: Callable) -> None:
    """Worker process main loop: run jobs until the None sentinel arrives.

    Args:
        jobs: mp.Queue of (run_id, request) tuples, or None to exit.
        finished: mp.Value counting completed jobs (read by the manager).
        target: Picklable job function, called as target(run_id, request).
    """
    while (job := jobs.get()) is not None:
        try:
            target(*job)
        finally:
            with finished.get_lock():
                finished.value += 1


@dataclass
class _Slot:
    """One session's worker process and its bookkeeping."""

    proc: mp.Process
    jobs: object  # mp.Queue
    finished: object  # mp.Value("i")
    submitted: int = 0
    last_used: float = field(default_factory=time.monotonic)

    @property
    def busy(self) -> bool:
        """True while any submitted job has not finished."""
        return self.submitted > self.finished.value


class WorkerManager:
    """Per-session worker processes (see module docstring)."""

    def __init__(self, max_workers: int, idle_s: float, target: Callable | None = None):
        """
        Args:
            max_workers: Maximum number of live worker processes.
            idle_s: Seconds without unfinished jobs before a worker is retired.
            target: Job function (module-level, picklable); defaults to `execute`.
        """
        self.max_workers, self.idle_s = max_workers, idle_s
        self.target = target or execute
        self.ctx = mp.get_context("spawn")
        self.slots: dict[str, _Slot] = {}
        self.retiring: list[mp.Process] = []
        self.lock = threading.Lock()

    def _start(self) -> _Slot:
        """Spawn a fresh worker process."""
        jobs, finished = self.ctx.Queue(), self.ctx.Value("i", 0)
        proc = self.ctx.Process(target=_serve, args=(jobs, finished, self.target), daemon=True)
        proc.start()
        return _Slot(proc, jobs, finished)

    def _retire(self, session: str) -> None:
        """Ask a worker to exit after its queued jobs (non-blocking)."""
        slot = self.slots.pop(session)
        slot.jobs.put(None)
        self.retiring.append(slot.proc)

    def reap(self) -> None:
        """Drop dead workers, retire idle ones and join exited processes."""
        now = time.monotonic()
        for session, slot in list(self.slots.items()):
            if not slot.proc.is_alive():
                self.slots.pop(session)
            elif not slot.busy and now - slot.last_used > self.idle_s:
                self._retire(session)
        self.retiring = [p for p in self.retiring if p.is_alive()]

    def _make_room(self) -> None:
        """Evict the longest-idle worker, or raise CapacityError if all are busy."""
        idle = [(s.last_used, k) for k, s in self.slots.items() if not s.busy]
        if not idle:
            raise CapacityError(f"all {self.max_workers} workers are busy")
        self._retire(min(idle)[1])

    def submit(self, session: str, run_id: str, request: dict) -> None:
        """Queue a run on the session's worker, starting one if needed.

        Args:
            session: Session id (one worker per session).
            run_id: Run id (folder already created in the run store).
            request: RunRequest as a plain dict (picklable).
        Raises:
            CapacityError: if no worker slot can be freed.
        """
        with self.lock:
            self.reap()
            slot = self.slots.get(session)
            if slot is None:
                if len(self.slots) >= self.max_workers:
                    self._make_room()
                slot = self.slots[session] = self._start()
            slot.jobs.put((run_id, request))
            slot.submitted += 1
            slot.last_used = time.monotonic()

    def shutdown(self) -> None:
        """Terminate every worker (API shutdown)."""
        with self.lock:
            procs = [s.proc for s in self.slots.values()] + self.retiring
            self.slots, self.retiring = {}, []
        for p in procs:
            p.terminate()
            p.join(timeout=2)


_manager: WorkerManager | None = None


def manager() -> WorkerManager:
    """Process-wide WorkerManager built from settings (created lazily)."""
    global _manager
    if _manager is None:
        s = get_settings()
        _manager = WorkerManager(s.max_workers, s.worker_idle_timeout_s)
    return _manager


def submit(session: str, run_id: str, request: dict) -> None:
    """Queue a run on the session's own worker (see WorkerManager.submit)."""
    manager().submit(session, run_id, request)


def shutdown() -> None:
    """Stop all workers (called on API shutdown)."""
    global _manager
    if _manager is not None:
        _manager.shutdown()
        _manager = None


def _network_json(base_net, net, G, area_name: str) -> dict:
    """Network GeoJSON of the run (scenario network, labelled against the baseline)."""
    import json

    from app.runs.export import network_geojson
    from app.sim.recorder import FrameRecorder

    geo = network_geojson(net, FrameRecorder(net, None).origin, base_net)
    geo["meta"]["area"] = area_name
    if "bbox" in G.graph:
        s, w, n, e = json.loads(G.graph["bbox"])
        geo["meta"]["area_bbox"] = [w, s, e, n]
    return geo


def _simulate(run_id: str, req, G, area_name: str) -> dict:
    """Build the graphs, write the network and run baseline (+ scenario).

    Args:
        run_id: Run id.
        req: Validated RunRequest.
        G: Cached road graph of the area.
        area_name: Area label for the UI.
    Returns:
        run_pair() result with `info.area` and `info.edit_usage`.
    """
    from app.runs import store
    from app.scenario.builder import build_graphs, edit_usage
    from app.sim.engine import SimConfig
    from app.sim.network import compile_network
    from app.sim.runner import run_pair

    d = store.run_dir(run_id)
    G_base, Gs, manifest = build_graphs(G, req)
    base_net = compile_network(G_base)
    net = compile_network(Gs, base_net) if Gs is not None else base_net
    store.write_json(d / "network.json", _network_json(base_net, net, G, area_name))
    store.set_status(run_id, "running", "simulating")
    cfg = SimConfig(duration_s=req.duration_s, warmup_s=req.warmup_s, dt=req.dt,
                    seed=req.seed, signal_stop_s=req.signal_stop_s)
    result = run_pair(G_base, Gs, req.traffic, req.hour, cfg, d)
    result["info"]["area"] = area_name
    if result["scenario"]:
        result["info"]["edit_usage"] = edit_usage(manifest, net, result["scenario"])
    return result


def execute(run_id: str, request: dict) -> None:
    """Run one job end to end inside a worker process (see module docstring)."""
    from app.api.schemas import RunRequest
    from app.geo.geocoder import resolve_area
    from app.geo.graph_builder import load_or_build
    from app.geo.overpass import OverpassUnavailable
    from app.runs import store
    from app.scenario.edits import EditError

    d = store.run_dir(run_id)
    try:
        req = RunRequest.model_validate(request)
        store.set_status(run_id, "running", "geocoding")
        area = resolve_area(req.place)
        store.set_status(run_id, "running", "loading road network")
        G = load_or_build(area, on_stage=lambda s: store.set_status(run_id, "running", s))
        store.write_json(d / "result.json", _simulate(run_id, req, G, area.name))
        store.set_status(run_id, "done")
    except (OverpassUnavailable, EditError) as exc:  # expected failures: plain message
        store.set_status(run_id, "error", error=str(exc))
    except Exception as exc:  # report any failure to the UI instead of dying silently
        traceback.print_exc()
        store.set_status(run_id, "error", error=f"{type(exc).__name__}: {exc}")
