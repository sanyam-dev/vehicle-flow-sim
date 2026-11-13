"""Shared fixtures: an isolated data dir and an offline "Test Grid" area.

The API tests run real worker processes. They stay offline because:

* `DATA_DIR` points at a temp folder (spawned workers inherit the env var);
* the geocoder cache is pre-filled for TEST_PLACE (and an empty answer for
  MISSING_PLACE) by calling the real geocoder with a fake HTTP response;
* the synthetic grid is saved as that area's cached GraphML graph.
"""

import json

import httpx
import osmnx as ox
import pytest
from fastapi.testclient import TestClient
from pyproj import Transformer

from app.config import get_settings
from tests.synthetic import CRS, GRID_STEP_M, X0, Y0, grid

TEST_PLACE = "Test Grid"
MISSING_PLACE = "Nowhere At All"


class _FakeResponse:
    """Minimal stand-in for an httpx.Response from Nominatim."""

    def __init__(self, payload: list[dict]):
        self.payload = payload

    def raise_for_status(self) -> None:
        """Never fails."""

    def json(self) -> list[dict]:
        """Return the canned Nominatim results."""
        return self.payload


def _nominatim_hit() -> dict:
    """Nominatim-like result whose bbox surrounds the synthetic grid."""
    to_ll = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
    lon, lat = to_ll.transform(X0 + GRID_STEP_M, Y0 + GRID_STEP_M)
    d = 0.003
    return {"display_name": TEST_PLACE, "address": {"country_code": "in"},
            "boundingbox": [str(lat - d), str(lat + d), str(lon - d), str(lon + d)]}


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point settings (here and in spawned workers) at an empty temp data dir."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def grid_area(data_dir, monkeypatch):
    """Register TEST_PLACE offline: geocoder cache + cached graph.

    Returns:
        The resolved `Area` of TEST_PLACE.
    """
    from app.geo.geocoder import resolve_area

    answers = {TEST_PLACE: [_nominatim_hit()], MISSING_PLACE: []}
    monkeypatch.setattr(httpx, "get", lambda url, params, **kw: _FakeResponse(
        answers[params["q"]]))
    area = resolve_area(TEST_PLACE)
    try:
        resolve_area(MISSING_PLACE)
    except Exception:  # GeocodeError; the empty answer is cached all the same
        pass
    G = grid()
    G.graph.update(area_id=area.area_id, area_name=area.name)
    ox.save_graphml(G, get_settings().path("graphs", f"{area.area_id}.graphml"))
    monkeypatch.undo()  # restore httpx.get; keep DATA_DIR (set again below)
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    return area


@pytest.fixture
def client(grid_area):
    """TestClient on a fresh app; workers are shut down afterwards."""
    from app.main import create_app
    from app.runs import worker

    worker.shutdown()  # drop any manager built with other settings
    with TestClient(create_app()) as c:
        yield c
    worker.shutdown()


def dump(obj) -> str:
    """Compact JSON for assertion messages."""
    return json.dumps(obj)[:2000]
