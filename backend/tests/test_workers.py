"""WorkerManager unit tests with a trivial job (fast, no simulation)."""

import time

import pytest

from app.runs.worker import CapacityError, WorkerManager
from tests.jobs import record


def _lines(path, n: int, timeout_s: float = 30.0) -> list[list[str]]:
    """Wait until `path` has n lines; return them split into (run_id, pid)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.exists() and len(rows := path.read_text().split("\n")[:-1]) >= n:
            return [r.split() for r in rows]
        time.sleep(0.05)
    raise AssertionError(f"{path} did not reach {n} lines")


@pytest.fixture
def mgr():
    """Manager with two slots and a 0.2 s idle timeout."""
    m = WorkerManager(max_workers=2, idle_s=0.2, target=record)
    yield m
    m.shutdown()


def test_jobs_of_one_session_run_in_order_in_one_process(mgr, tmp_path):
    log = tmp_path / "log"
    for i in range(3):
        mgr.submit("a", f"r{i}", {"log": str(log)})
    rows = _lines(log, 3)
    assert [r[0] for r in rows] == ["r0", "r1", "r2"]
    assert len({r[1] for r in rows}) == 1


def test_sessions_run_in_parallel_processes(mgr, tmp_path):
    log = tmp_path / "log"
    t0 = time.monotonic()
    mgr.submit("a", "slow", {"log": str(log), "sleep": 3.0})
    mgr.submit("b", "fast", {"log": str(log)})
    rows = _lines(log, 2)
    assert rows[0][0] == "fast"  # b was not blocked behind a's slow job
    assert rows[0][1] != rows[1][1]
    assert time.monotonic() - t0 < 15


def test_idle_worker_is_retired_and_restarted(mgr, tmp_path):
    log = tmp_path / "log"
    mgr.submit("a", "r0", {"log": str(log)})
    first_pid = _lines(log, 1)[0][1]
    time.sleep(0.5)
    mgr.reap()
    assert "a" not in mgr.slots
    mgr.submit("a", "r1", {"log": str(log)})
    assert _lines(log, 2)[1][1] != first_pid


def test_idle_worker_is_evicted_when_full_busy_ones_are_not(mgr, tmp_path):
    log = tmp_path / "log"
    mgr.idle_s = 3600  # never retire by time in this test
    mgr.submit("a", "r0", {"log": str(log)})
    _lines(log, 1)  # a is now idle
    mgr.submit("b", "slow", {"log": str(log), "sleep": 5.0})
    mgr.submit("c", "r1", {"log": str(log), "sleep": 5.0})  # evicts idle a
    assert set(mgr.slots) == {"b", "c"}
    with pytest.raises(CapacityError):
        mgr.submit("d", "r2", {"log": str(log)})  # b and c both busy
