"""OSM road graph for an Area, built with osmnx and cached per area.

Pipeline: download (unsimplified, drive network, truncated by edge)
  -> remember traffic-signal points -> simplify -> project to UTM
  -> consolidate complex intersections -> save GraphML cache.

Signals are often tagged on nodes a few metres before a junction, which
simplification removes, so we keep their projected coordinates in the
graph attribute `signals` and match them to junctions later.
"""

import json
import logging
import tempfile
from pathlib import Path

import networkx as nx
import osmnx as ox
from pyproj import Transformer

from app.config import get_settings
from app.geo import overpass
from app.geo.geocoder import Area
from app.geo.overpass import StageFn

log = logging.getLogger(__name__)

CONSOLIDATE_TOLERANCE_M = 12.0
EXTRA_WAY_TAGS = ["layer", "lanes:forward", "lanes:backward", "turn:lanes"]


def _configure_osmnx() -> None:
    """Register the extra OSM way tags we need (read by `graph_from_xml`)."""
    for tag in EXTRA_WAY_TAGS:
        if tag not in ox.settings.useful_tags_way:
            ox.settings.useful_tags_way.append(tag)


def _signal_points(G: nx.MultiDiGraph) -> list[tuple[float, float]]:
    """(lon, lat) of every node tagged highway=traffic_signals.

    Args:
        G: Unsimplified, unprojected osmnx graph.
    """
    return [(d["x"], d["y"]) for _, d in G.nodes(data=True)
            if "traffic_signals" in str(d.get("highway", ""))]


def graph_from_osm_xml(xml: bytes, bbox: tuple[float, float, float, float]) -> nx.MultiDiGraph:
    """Unsimplified drive graph from Overpass XML, as `ox.graph_from_bbox` built it.

    Args:
        xml: OSM XML of every drivable way touching the box (see overpass.build_query).
        bbox: (west, south, east, north) in degrees.
    Returns:
        Unsimplified, unprojected graph: truncated to the box (keeping edges
        with one end inside) and reduced to its largest weakly connected part.
    """
    _configure_osmnx()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "area.osm"
        path.write_bytes(xml)
        G = ox.graph_from_xml(path, simplify=False, retain_all=True)
    G = ox.truncate.truncate_graph_bbox(G, bbox, truncate_by_edge=True)
    return ox.truncate.largest_component(G)


def _fetch_raw(bbox: tuple[float, float, float, float],
               on_stage: StageFn | None = None) -> nx.MultiDiGraph:
    """Download the unsimplified drive network (time-bounded, see geo/overpass.py).

    Args:
        bbox: (west, south, east, north) in degrees.
        on_stage: Optional progress callback.
    Returns:
        Unsimplified, unprojected osmnx graph.
    Raises:
        overpass.OverpassUnavailable: if no server answered in time.
    """
    return graph_from_osm_xml(overpass.fetch_xml(bbox, on_stage), bbox)


def _download(area: Area, on_stage: StageFn | None = None) -> nx.MultiDiGraph:
    """Download, simplify, project and consolidate the drive network.

    Args:
        area: Simulation area (bbox in degrees).
        on_stage: Optional progress callback.
    Returns:
        Projected, consolidated MultiDiGraph with graph attrs
        `signals` (JSON list of projected xy), `driving_side`, `area_id`, `bbox`.
    """
    _configure_osmnx()
    bbox = (area.west, area.south, area.east, area.north)
    G = _fetch_raw(bbox, on_stage)
    if on_stage:
        on_stage("building road network")
    signals_ll = _signal_points(G)
    G = ox.simplify_graph(G)
    Gp = ox.project_graph(G)
    to_xy = Transformer.from_crs("EPSG:4326", Gp.graph["crs"], always_xy=True)
    signals_xy = [to_xy.transform(lon, lat) for lon, lat in signals_ll]
    Gc = ox.consolidate_intersections(
        Gp, tolerance=CONSOLIDATE_TOLERANCE_M, rebuild_graph=True, dead_ends=True)
    Gc.graph.update(
        signals=json.dumps(signals_xy), driving_side=area.driving_side,
        area_id=area.area_id, area_name=area.name,
        bbox=json.dumps([area.south, area.west, area.north, area.east]))
    return Gc


def load_or_build(area: Area, on_stage: StageFn | None = None) -> nx.MultiDiGraph:
    """Return the cached graph for `area`, downloading it on first use.

    Args:
        area: Simulation area.
        on_stage: Optional progress callback (download server/try, build step).
    Returns:
        Projected, consolidated road graph (see `_download`).
    Raises:
        overpass.OverpassUnavailable: if the download failed within its time budget.
    """
    path = get_settings().path("graphs", f"{area.area_id}.graphml")
    if path.exists():
        return ox.load_graphml(path)
    G = _download(area, on_stage)
    ox.save_graphml(G, path)
    return ox.load_graphml(path)


def graph_signals(G: nx.MultiDiGraph) -> list[tuple[float, float]]:
    """Projected (x, y) traffic-signal points stored on the graph."""
    return [tuple(p) for p in json.loads(G.graph.get("signals", "[]"))]
