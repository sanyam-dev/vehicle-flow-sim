"""API tests: simulations driven end to end through the HTTP endpoints.

Runs execute in real spawned worker processes against the offline
"Test Grid" area (see conftest.py).
"""

import time

import pytest

from app.config import get_settings
from app.runs import worker
from tests.conftest import MISSING_PLACE, TEST_PLACE, dump

SESSION_A = {"X-Session-Id": "session-aaaa-1111"}
SESSION_B = {"X-Session-Id": "session-bbbb-2222"}
QUICK = {"place": TEST_PLACE, "traffic": "moderate", "time": "09:00",
         "duration_s": 180, "warmup_s": 60}


def wait_done(client, run_id: str, timeout_s: float = 120.0) -> dict:
    """Poll GET /api/runs/{id} until the run is done or failed.

    Returns:
        The final run document.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        doc = client.get(f"/api/runs/{run_id}").json()
        if doc["state"] in ("done", "error"):
            return doc
        time.sleep(0.25)
    raise AssertionError(f"run {run_id} still {doc['state']} after {timeout_s}s")


def start(client, body: dict, headers: dict = SESSION_A) -> str:
    """POST /api/runs and return the run id (asserts 202)."""
    r = client.post("/api/runs", json=body, headers=headers)
    assert r.status_code == 202, r.text
    return r.json()["id"]


# ---- validation and errors (no simulation) ---------------------------------------

def test_health(client):
    assert client.get("/api/health").json() == {"ok": True}


@pytest.mark.parametrize("patch", [{"time": "25:00"}, {"duration_s": 10},
                                   {"traffic": "insane"}, {"place": ""},
                                   {"close_lanes": [{"road": "x"}]}])
def test_invalid_run_requests_are_rejected(client, patch):
    r = client.post("/api/runs", json={**QUICK, **patch}, headers=SESSION_A)
    assert r.status_code == 422


def test_malformed_session_header_is_rejected(client):
    r = client.post("/api/runs", json=QUICK, headers={"X-Session-Id": "bad id!"})
    assert r.status_code == 400


def test_unknown_and_malformed_run_ids(client):
    assert client.get("/api/runs/abcdef123456").status_code == 404
    assert client.get("/api/runs/..%2Fsecrets").status_code in (400, 404)
    assert client.get("/api/runs/NOT-VALID").status_code == 400


# ---- simulations through the API -------------------------------------------------

def test_baseline_run_end_to_end(client):
    """Queue -> worker -> done; result, network and frames are served."""
    run_id = start(client, QUICK)
    doc = wait_done(client, run_id)
    assert doc["state"] == "done", dump(doc)
    assert doc["progress"] == 1.0
    kpi = doc["result"]["baseline"]["kpi"]
    assert kpi["trips_completed"] > 0 and kpi["unserved_trips"] == 0
    assert doc["result"]["scenario"] is None

    geo = client.get(f"/api/runs/{run_id}/network").json()
    kinds = {f["properties"]["kind"] for f in geo["features"]}
    assert {"edge", "signal", "boundary"} <= kinds

    meta = client.get(f"/api/runs/{run_id}/baseline/meta").json()
    assert meta["n_frames"] == 180 and meta["n_chunks"] == 3
    chunk = client.get(f"/api/runs/{run_id}/baseline/chunks/0").json()
    assert len(chunk["t"]) == 60 and len(chunk["off"]) == 61
    assert len(chunk["vid"]) == chunk["off"][-1] == len(chunk["x"])
    assert client.get(f"/api/runs/{run_id}/baseline/chunks/99").status_code == 404
    assert client.get(f"/api/runs/{run_id}/scenario/meta").status_code == 404


def test_scenario_run_compares_with_baseline(client):
    """A road closure produces a scenario, scenario frames and a comparison."""
    run_id = start(client, {**QUICK, "close_roads": ["Main Street"]})
    doc = wait_done(client, run_id)
    assert doc["state"] == "done", dump(doc)
    res = doc["result"]
    assert res["comparison"]["kpi"]["unserved_trips"]["scenario"] > 0
    assert res["info"]["routes_recomputed"] > 0
    assert client.get(f"/api/runs/{run_id}/scenario/meta").status_code == 200
    closed = [f for f in client.get(f"/api/runs/{run_id}/network").json()["features"]
              if f["properties"].get("closed")]
    assert closed and all(f["properties"]["name"] == "Main Street" for f in closed)


def test_same_request_same_metrics(client):
    """Fixed seed through the API: two identical requests give identical KPIs."""
    a, b = start(client, QUICK), start(client, QUICK)
    ra, rb = wait_done(client, a), wait_done(client, b)
    assert ra["result"]["baseline"]["kpi"] == rb["result"]["baseline"]["kpi"]


def test_unknown_place_reports_error(client):
    """A place the geocoder cannot find ends in state "error" with a message."""
    doc = wait_done(client, start(client, {**QUICK, "place": MISSING_PLACE}))
    assert doc["state"] == "error"
    assert "GeocodeError" in doc["error"]


# ---- per-session worker processes ------------------------------------------------

def test_each_session_gets_its_own_worker(client):
    """Two sessions -> two live worker processes; one session reuses its worker."""
    ids = [start(client, QUICK, SESSION_A), start(client, QUICK, SESSION_B)]
    slots = worker.manager().slots
    assert set(slots) == {"s:session-aaaa-1111", "s:session-bbbb-2222"}
    pid_a = slots["s:session-aaaa-1111"].proc.pid
    assert pid_a != slots["s:session-bbbb-2222"].proc.pid
    for run_id in ids:
        assert wait_done(client, run_id)["state"] == "done"
    start(client, QUICK, SESSION_A)
    assert worker.manager().slots["s:session-aaaa-1111"].proc.pid == pid_a


def test_full_capacity_returns_503(client, monkeypatch):
    """With one worker slot busy, another session gets 503 + Retry-After."""
    monkeypatch.setattr(get_settings(), "max_workers", 1)
    worker.shutdown()
    start(client, {**QUICK, "duration_s": 900}, SESSION_A)
    r = client.post("/api/runs", json=QUICK, headers=SESSION_B)
    assert r.status_code == 503
    assert r.headers["Retry-After"] == str(worker.RETRY_AFTER_S)


# ---- built frontend served by the API --------------------------------------------

def test_built_frontend_is_served_same_origin(grid_area, tmp_path, monkeypatch):
    """With a built frontend present, / serves it and /api still answers."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<title>Traffic Sim Studio</title>")
    (dist / "assets" / "app-abc123.js").write_text("console.log(1)")
    monkeypatch.setattr(get_settings(), "static_dir", dist)
    with TestClient(create_app()) as c:
        assert "Traffic Sim Studio" in c.get("/").text
        asset = c.get("/assets/app-abc123.js")
        assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]
        assert c.get("/api/health").json() == {"ok": True}
        assert c.get("/missing.js").status_code == 404


def test_llm_warm_runs_once_per_interval(client, monkeypatch):
    from app.llm import agent

    sent = []
    monkeypatch.setattr(agent, "_last_warm", float("-inf"))
    monkeypatch.setattr(agent, "warm", lambda: sent.append(1))
    first = client.post("/api/llm/warm")
    second = client.post("/api/llm/warm")
    assert first.status_code == 202 and first.json() == {"warming": True}
    assert second.json() == {"warming": False}
    assert sent == [1]  # the background task ran once
