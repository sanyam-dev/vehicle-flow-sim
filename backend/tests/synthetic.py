"""Small synthetic road graphs for engine tests (projected metres, no OSM)."""

import json

import networkx as nx
import numpy as np
from shapely.geometry import LineString

CRS = "EPSG:32643"  # UTM 43N (Bengaluru)
X0, Y0 = 780000.0, 1430000.0


def make_graph(nodes: dict[int, tuple[float, float]], roads: list[tuple], side: str = "left",
               signals: list[int] | None = None) -> nx.MultiDiGraph:
    """Build an osmnx-like projected MultiDiGraph.

    Args:
        nodes: id -> (x, y) offsets in metres.
        roads: (u, v, attrs) per directed edge; attrs need at least `highway`
            and `lanes` (per direction, stored as `n_lanes`).
        side: Driving side.
        signals: Node ids that carry a traffic signal.
    Returns:
        Graph compatible with `sim.network.compile_network`.
    """
    G = nx.MultiDiGraph(crs=CRS, driving_side=side, area_id="synthetic")
    for n, (x, y) in nodes.items():
        G.add_node(n, x=X0 + x, y=Y0 + y)
    for u, v, attrs in roads:
        a, b = np.array(nodes[u]) + (X0, Y0), np.array(nodes[v]) + (X0, Y0)
        d = {"oneway": True, "maxspeed": "50", "geometry": LineString([a, b]),
             "length": float(np.linalg.norm(b - a))}
        d.update(attrs)
        d["n_lanes"] = d.pop("lanes", 1)
        G.add_edge(u, v, **d)
    pts = [[X0 + nodes[n][0], Y0 + nodes[n][1]] for n in (signals or [])]
    G.graph["signals"] = json.dumps(pts)
    return G


def corridor(length_m: float = 1500.0, lanes: int = 1, mid_node: bool = True):
    """A one-way road west->east, optionally split by a pass-through node."""
    if mid_node:
        nodes = {1: (0, 0), 2: (length_m / 2, 0), 3: (length_m, 0)}
        roads = [(1, 2, {"highway": "primary", "lanes": lanes}),
                 (2, 3, {"highway": "primary", "lanes": lanes})]
    else:
        nodes = {1: (0, 0), 3: (length_m, 0)}
        roads = [(1, 3, {"highway": "primary", "lanes": lanes})]
    return make_graph(nodes, roads)


GRID_STEP_M = 300.0
STUB_M = 200.0


def _two_way(roads: list, u: int, v: int, attrs: dict) -> None:
    """Append both directions of a road with the same attributes."""
    roads.append((u, v, dict(attrs)))
    roads.append((v, u, dict(attrs)))


def grid(signal_centre: bool = True) -> nx.MultiDiGraph:
    """A 3x3 junction grid (300 m blocks) with 200 m entry/exit stubs.

    Node ids: junction (i, j) -> 10 * i + j (i = column west->east,
    j = row south->north); stub ends are 100 + n. Row 1 is "Main Street"
    and column 1 "Centre Avenue" (primary, 2 lanes per direction); the
    rest are 1-lane residential "Side Road"s. The centre junction (11)
    is signalised when `signal_centre` is True.

    Returns:
        Graph compatible with `sim.network.compile_network`.
    """
    nodes = {10 * i + j: (i * GRID_STEP_M, j * GRID_STEP_M) for i in range(3) for j in range(3)}
    main = {"highway": "primary", "lanes": 2, "name": "Main Street"}
    avenue = {"highway": "primary", "lanes": 2, "name": "Centre Avenue"}
    side = {"highway": "residential", "lanes": 1, "name": "Side Road", "maxspeed": "30"}
    roads: list = []
    for i in range(3):
        for j in range(3):
            n = 10 * i + j
            if i < 2:
                _two_way(roads, n, n + 10, main if j == 1 else side)
            if j < 2:
                _two_way(roads, n, n + 1, avenue if i == 1 else side)
    for n, (x, y) in list(nodes.items()):
        i, j = divmod(n, 10)
        for di, dj in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            if 0 <= i + di <= 2 and 0 <= j + dj <= 2:
                continue  # inner side, no stub
            stub = 100 + n + (1000 if di or dj > 0 else 0) * (1 + di + 2 * dj + 4)
            nodes[stub] = (x + di * STUB_M, y + dj * STUB_M)
            road = (main if j == 1 and di else avenue if i == 1 and dj else side)
            _two_way(roads, n, stub, road)
    return make_graph(nodes, roads, signals=[11] if signal_centre else [])
