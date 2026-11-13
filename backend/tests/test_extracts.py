"""Offline Geofabrik extract fallback: .poly parsing, region choice, drive
filter and the Overpass -> extract hand-over (offline)."""

import shutil
import subprocess

import pytest

from app.config import get_settings
from app.geo import extracts, graph_builder, overpass
from app.geo.overpass import OverpassUnavailable
from tests.test_overpass import BBOX, OSM_XML


def _poly(name: str, rings: list[tuple[str, list[tuple[float, float]]]]) -> str:
    """Write rings in osmosis .poly format."""
    out = [name]
    for ring_name, pts in rings:
        out += [ring_name, *(f"   {x} {y}" for x, y in pts), "END"]
    return "\n".join([*out, "END"]) + "\n"


def _square(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


@pytest.fixture
def extract_dir(tmp_path, monkeypatch, data_dir):
    """A country (0..2 x 0..1) split into zone_a (0..1, with a hole) and zone_b (1..2)."""
    d = tmp_path / "extracts"
    d.mkdir()
    polys = {
        "country": [("1", _square(0, 0, 2, 1))],
        "zone_a": [("1", _square(0, 0, 1, 1)), ("!2", _square(0.4, 0.4, 0.6, 0.6))],
        "zone_b": [("1", _square(1, 0, 2, 1))],
    }
    for rid, rings in polys.items():
        (d / f"{rid}.poly").write_text(_poly(rid, rings))
        (d / f"{rid}.osm.pbf").write_bytes(b"")
    (d / "zone_a.date").write_text("2026-11-12T21:23:05Z\n")
    (d / "orphan.osm.pbf").write_bytes(b"")  # no .poly: ignored
    monkeypatch.setenv("OSM_EXTRACTS_DIR", str(d))
    monkeypatch.setattr(extracts.shutil, "which", lambda name: f"/usr/bin/{name}")
    get_settings.cache_clear()
    extracts.regions.cache_clear()
    yield d
    extracts.regions.cache_clear()
    get_settings.cache_clear()


def test_parse_poly_handles_holes():
    shape = extracts.parse_poly(_poly("x", [("1", _square(0, 0, 1, 1)),
                                            ("!2", _square(0.4, 0.4, 0.6, 0.6))]))
    assert shape.area == pytest.approx(1 - 0.04)


def test_regions_need_a_poly_and_read_the_date(extract_dir):
    found = {r.id: r for r in extracts.regions(str(extract_dir))}
    assert sorted(found) == ["country", "zone_a", "zone_b"]
    assert found["zone_a"].osm_date == "2026-11-12T21:23:05Z"
    assert found["zone_b"].osm_date == "unknown"


@pytest.mark.parametrize("bbox, expected", [
    ((0.1, 0.1, 0.2, 0.2), "zone_a"),    # inside one zone -> the zone
    ((1.5, 0.1, 1.6, 0.2), "zone_b"),
    ((0.9, 0.1, 1.1, 0.2), "country"),   # straddles two zones -> the country file
    ((0.45, 0.45, 0.55, 0.55), "country"),  # in zone_a's hole
    ((3.0, 3.0, 3.1, 3.1), None),        # outside every region
])
def test_covering_picks_the_smallest_containing_region(extract_dir, bbox, expected):
    region = extracts.covering(bbox)
    assert (region.id if region else None) == expected


def test_no_fallback_without_folder_or_osmium(extract_dir, monkeypatch):
    monkeypatch.setattr(extracts.shutil, "which", lambda name: None)
    assert extracts.covering((0.1, 0.1, 0.2, 0.2)) is None
    monkeypatch.delenv("OSM_EXTRACTS_DIR")
    get_settings.cache_clear()
    assert extracts.covering((0.1, 0.1, 0.2, 0.2)) is None


# ---- drive filter ------------------------------------------------------------------

def _way(wid: int, a: int, b: int, **tags) -> str:
    tag_xml = "".join(f'<tag k="{k}" v="{v}"/>' for k, v in tags.items())
    return f'<way id="{wid}"><nd ref="{a}"/><nd ref="{b}"/>{tag_xml}</way>'


MIXED_XML = ("""<?xml version="1.0" encoding="UTF-8"?><osm version="0.6">"""
             + "".join(f'<node id="{i}" lat="17.00{i}" lon="78.00{i}"/>' for i in range(1, 8))
             + _way(10, 1, 2, highway="primary")
             + _way(11, 2, 3, highway="footway")
             + _way(12, 3, 4, highway="service", service="parking_aisle")
             + _way(13, 4, 5, highway="residential", service="driveway")
             + _way(14, 5, 6, highway="primary", access="private")
             + _way(15, 6, 7, highway="residential", motor_vehicle="no")
             + _way(16, 2, 7, highway="proposed")
             + "</osm>").encode()


def test_drive_filter_keeps_only_drivable_ways():
    G = graph_builder.graph_from_osm_xml(MIXED_XML, BBOX)
    assert sorted((u, v) for u, v, _ in G.edges) == [(1, 2), (2, 1)]


# ---- Overpass -> extract hand-over ------------------------------------------------

@pytest.fixture
def overpass_down(monkeypatch):
    """Overpass always fails; records the budget it was given."""
    budgets = []

    def fail(bbox, on_stage=None, *, budget_s=None):
        budgets.append(budget_s)
        raise OverpassUnavailable("all servers busy")

    monkeypatch.setattr(overpass, "fetch_xml", fail)
    return budgets


def test_extract_takes_over_when_overpass_fails(overpass_down, monkeypatch):
    region = extracts.Region("india-southern-zone", None, None, "2026-11-12")
    monkeypatch.setattr(extracts, "covering", lambda bbox: region)
    monkeypatch.setattr(extracts, "cut", lambda r, bbox: OSM_XML)
    stages = []
    G = graph_builder._fetch_raw(BBOX, stages.append)
    assert sorted(G.nodes) == [1, 2, 3, 4]
    assert overpass_down == [get_settings().overpass_budget_with_fallback_s]
    assert "offline OSM roads (india-southern-zone, data of 2026-11-12)" in stages[-1]


def test_overpass_error_surfaces_when_no_extract_covers(overpass_down, monkeypatch):
    monkeypatch.setattr(extracts, "covering", lambda bbox: None)
    with pytest.raises(OverpassUnavailable):
        graph_builder._fetch_raw(BBOX)
    assert overpass_down == [None]  # full Overpass budget


@pytest.mark.skipif(shutil.which("osmium") is None, reason="osmium-tool not installed")
def test_real_osmium_cut_round_trip(tmp_path):
    src = tmp_path / "fixture.osm"
    src.write_bytes(OSM_XML)
    pbf = tmp_path / "zone.osm.pbf"
    subprocess.run(["osmium", "cat", str(src), "-o", str(pbf)], check=True)
    region = extracts.Region("zone", pbf, None, "unknown")
    G = graph_builder.graph_from_osm_xml(extracts.cut(region, BBOX), BBOX)
    assert sorted(G.nodes) == [1, 2, 3, 4]
    assert len(graph_builder._signal_points(G)) == 1
