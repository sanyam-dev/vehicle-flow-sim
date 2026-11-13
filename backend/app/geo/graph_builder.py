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
import time

import networkx as nx
import osmnx as ox
from pyproj import Transformer

from app.config import get_settings
from app.geo.geocoder import Area

log = logging.getLogger(__name__)

CONSOLIDATE_TOLERANCE_M = 12.0
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api",
    "https://overpass.private.coffee/api",
    "https://overpass.kumi.systems/api",
]
MAX_ATTEMPTS = 6
EXTRA_WAY_TAGS = ["layer", "lanes:forward", "lanes:backward", "turn:lanes"]


def _configure_osmnx() -> None:
    """Set osmnx cache folder and the extra OSM tags we need."""
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(get_settings().path("cache", "osmnx", "x").parent)
    ox.settings.http_user_agent = get_settings().user_agent
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


def _fetch_raw(bbox: tuple[float, float, float, float]) -> nx.MultiDiGraph:
    """Download the unsimplified drive network, retrying across Overpass mirrors.

    Public Overpass servers are often at capacity; we rotate mirrors and back
    off exponentially (2, 4, 8 ... s) before giving up.

    Args:
        bbox: (west, south, east, north) in degrees.
    Returns:
        Unsimplified, unprojected osmnx graph.
    """
    delay = 2.0
    for attempt in range(MAX_ATTEMPTS):
        ox.settings.overpass_url = OVERPASS_MIRRORS[attempt % len(OVERPASS_MIRRORS)]
        try:
            return ox.graph_from_bbox(bbox, network_type="drive", simplify=False,
                                      truncate_by_edge=True)
        except Exception as exc:  # osmnx raises several HTTP / parse error types
            if attempt == MAX_ATTEMPTS - 1:
                raise
            log.warning("Overpass %s failed (%s); retrying in %.0fs",
                        ox.settings.overpass_url, type(exc).__name__, delay)
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def _download(area: Area) -> nx.MultiDiGraph:
    """Download, simplify, project and consolidate the drive network.

    Args:
        area: Simulation area (bbox in degrees).
    Returns:
        Projected, consolidated MultiDiGraph with graph attrs
        `signals` (JSON list of projected xy), `driving_side`, `area_id`, `bbox`.
    """
    _configure_osmnx()
    bbox = (area.west, area.south, area.east, area.north)
    G = _fetch_raw(bbox)
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


def load_or_build(area: Area) -> nx.MultiDiGraph:
    """Return the cached graph for `area`, downloading it on first use.

    Args:
        area: Simulation area.
    Returns:
        Projected, consolidated road graph (see `_download`).
    """
    path = get_settings().path("graphs", f"{area.area_id}.graphml")
    if path.exists():
        return ox.load_graphml(path)
    G = _download(area)
    ox.save_graphml(G, path)
    return ox.load_graphml(path)


def graph_signals(G: nx.MultiDiGraph) -> list[tuple[float, float]]:
    """Projected (x, y) traffic-signal points stored on the graph."""
    return [tuple(p) for p in json.loads(G.graph.get("signals", "[]"))]
