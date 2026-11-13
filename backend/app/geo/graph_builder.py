"""OSM road graph for an Area, built with osmnx and cached per area.

Pipeline: download (Overpass, or an offline Geofabrik extract when Overpass
  fails; unsimplified, drive network, truncated by edge)
  -> remember traffic-signal points -> simplify -> project to UTM
  -> consolidate complex intersections -> save GraphML cache.

Signals are often tagged on nodes a few metres before a junction, which
simplification removes, so we keep their projected coordinates in the
graph attribute `signals` and match them to junctions later.
"""

import json
import logging
import re
import tempfile
from pathlib import Path

import networkx as nx
import osmnx as ox
from pyproj import Transformer

from app.config import get_settings
from app.geo import extracts, overpass
from app.geo.geocoder import Area
from app.geo.overpass import StageFn

log = logging.getLogger(__name__)

CONSOLIDATE_TOLERANCE_M = 12.0
# motor_vehicle / motorcar are needed by keep_drive_ways (drive filter).
EXTRA_WAY_TAGS = ["layer", "lanes:forward", "lanes:backward", "turn:lanes",
                  "motor_vehicle", "motorcar"]


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


def _excluded(value, pattern: re.Pattern) -> bool:
    """True if a tag value (string, or list after merging) matches the pattern."""
    values = value if isinstance(value, list) else [value]
    return any(pattern.search(str(v)) for v in values if v is not None)


def keep_drive_ways(G: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Drop edges that osmnx's "drive" network excludes (in place).

    Overpass answers are already filtered server-side (no-op there); offline
    extracts hold every `highway` way, including footways, parking aisles and
    private roads. Uses the same rules as the Overpass query (overpass.DRIVE_EXCLUDE).

    Args:
        G: Unsimplified graph from `ox.graph_from_xml`.
    Returns:
        The same graph, without non-drivable edges (isolated nodes remain;
        `largest_component` removes them).
    """
    rules = [(tag, re.compile(rx)) for tag, rx in overpass.DRIVE_EXCLUDE]
    drop = [(u, v, k) for u, v, k, d in G.edges(keys=True, data=True)
            if "highway" not in d
            or any(tag in d and _excluded(d[tag], rx) for tag, rx in rules)]
    G.remove_edges_from(drop)
    return G


def graph_from_osm_xml(xml: bytes, bbox: tuple[float, float, float, float]) -> nx.MultiDiGraph:
    """Unsimplified drive graph from OSM XML, as `ox.graph_from_bbox` built it.

    Args:
        xml: OSM XML of every way touching the box, with all its nodes
            (an Overpass answer or an offline extract cut).
        bbox: (west, south, east, north) in degrees.
    Returns:
        Unsimplified, unprojected graph of drivable ways: truncated to the box
        (keeping edges with one end inside) and reduced to its largest weakly
        connected part.
    """
    _configure_osmnx()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "area.osm"
        path.write_bytes(xml)
        G = ox.graph_from_xml(path, simplify=False, retain_all=True)
    G = keep_drive_ways(G)
    G = ox.truncate.truncate_graph_bbox(G, bbox, truncate_by_edge=True)
    return ox.truncate.largest_component(G)


def _fetch_raw(bbox: tuple[float, float, float, float],
               on_stage: StageFn | None = None) -> nx.MultiDiGraph:
    """Unsimplified drive network: Overpass first, offline extract as fallback.

    When a prepared extract covers the box, Overpass gets a shorter budget
    (`overpass_budget_with_fallback_s`) before the extract takes over.

    Args:
        bbox: (west, south, east, north) in degrees.
        on_stage: Optional progress callback.
    Returns:
        Unsimplified, unprojected osmnx graph.
    Raises:
        overpass.OverpassUnavailable: if Overpass failed and no extract covers the box.
    """
    region = extracts.covering(bbox)
    budget = get_settings().overpass_budget_with_fallback_s if region else None
    try:
        xml = overpass.fetch_xml(bbox, on_stage, budget_s=budget)
    except overpass.OverpassUnavailable as exc:
        if region is None:
            raise
        log.warning("Overpass failed (%s); using extract %s", exc, region.id)
        if on_stage:
            on_stage(f"Overpass busy; using offline OSM roads "
                     f"({region.id}, data of {region.osm_date})")
        xml = extracts.cut(region, bbox)
    return graph_from_osm_xml(xml, bbox)


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
