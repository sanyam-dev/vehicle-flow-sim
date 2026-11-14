"""Map-editor edits: clicked points, new roads, extra lanes, junction control.

The user clicks two points on the map; each is resolved here to a graph
node. A click near a node uses that node, a click on a road splits the road
there (a new pass-through node). Splits are applied to the *baseline* graph
too: a pass-through node changes nothing by itself, and it keeps edge ids
identical in both runs so their metrics stay comparable.

Edits are stored as graph attributes that `sim.network.compile_network`
understands:

* new flyover / underpass  edges with `added`, `grade`, `n_lanes`
* extra lane               `n_lanes` + 1 on the edges of the shortest path
* junction control         node attrs `control` ("signal" | "circle"), `signal_stop_s`
"""

import networkx as nx
import numpy as np
from pyproj import Transformer
from shapely.geometry import LineString, Point
from shapely.ops import substring

from app.geo.lanes import edge_lane_count

EdgeKey = tuple[int, int, int]
NODE_SNAP_M = 15.0   # a click this close to a node uses the node
EDGE_SNAP_M = 40.0   # a click further than this from every road is rejected
MIN_ROAD_M, MAX_ROAD_M = 50.0, 2500.0
MAX_EDGE_LANES = 6
TWIN_TOL_M = 6.0     # reverse edges this close are the same road's other direction
GRADES = {"flyover": "bridge", "underpass": "underpass"}
NEW_ROAD_SPEED_KMH = "60"


class EditError(ValueError):
    """A scenario edit cannot be applied; the message is shown to the user."""


def edge_line(G: nx.MultiDiGraph, u: int, v: int, k: int) -> LineString:
    """Geometry of a directed edge (a straight line when it has none)."""
    geom = G.edges[u, v, k].get("geometry")
    if geom is not None:
        return geom
    return LineString([(G.nodes[u]["x"], G.nodes[u]["y"]), (G.nodes[v]["x"], G.nodes[v]["y"])])


def to_xy(G: nx.MultiDiGraph, lat: float, lon: float) -> tuple[float, float]:
    """Project a WGS84 point into the graph's coordinate system (metres)."""
    return Transformer.from_crs("EPSG:4326", G.graph["crs"], always_xy=True).transform(lon, lat)


def _cut(G: nx.MultiDiGraph, edge: EdgeKey, new: int, d: float, splits: dict) -> None:
    """Replace `edge` by two edges meeting at node `new`, `d` metres along it."""
    u, v, k = edge
    line, attrs = edge_line(G, u, v, k), dict(G.edges[u, v, k])
    G.remove_edge(u, v, k)
    parts = []
    for a, b, d0, d1 in ((u, new, 0.0, d), (new, v, d, line.length)):
        key = G.add_edge(a, b, **{**attrs, "geometry": substring(line, d0, d1),
                                  "length": d1 - d0})
        parts.append((a, b, key))
    splits[edge] = parts


def split_edge(G: nx.MultiDiGraph, edge: EdgeKey, xy: tuple[float, float],
               splits: dict) -> int:
    """Split a road at the point nearest to `xy` (in place).

    The other direction of a two-way road is split at the same node.

    Args:
        G: Graph to modify.
        edge: Directed edge (u, v, key) to split.
        xy: Clicked point (projected metres).
        splits: Filled with old edge -> [first half, second half].
    Returns:
        The new node id, or an end node when the point is within NODE_SNAP_M of it.
    """
    u, v, k = edge
    line = edge_line(G, u, v, k)
    d = line.project(Point(xy))
    if d < NODE_SNAP_M or line.length - d < NODE_SNAP_M:
        return u if d < line.length - d else v
    at = line.interpolate(d)
    new = max(G.nodes) + 1
    G.add_node(new, x=at.x, y=at.y, street_count=2)
    twins = [(v, u, k2) for k2 in (G.get_edge_data(v, u) or {})
             if edge_line(G, v, u, k2).hausdorff_distance(line) < TWIN_TOL_M]
    _cut(G, edge, new, d, splits)
    for twin in twins:
        _cut(G, twin, new, edge_line(G, *twin).project(at), splits)
    return new


def resolve_point(G: nx.MultiDiGraph, lat: float, lon: float, splits: dict) -> int:
    """Graph node for a clicked point, splitting a road if needed (in place).

    Args:
        G: Graph to modify.
        lat, lon: Clicked point in degrees.
        splits: Split bookkeeping (see `split_edge`).
    Returns:
        Node id.
    Raises:
        EditError: if the point is further than EDGE_SNAP_M from every road.
    """
    xy = to_xy(G, lat, lon)
    ids = list(G.nodes)
    pts = np.asarray([[G.nodes[n]["x"], G.nodes[n]["y"]] for n in ids], dtype=float)
    dist = np.hypot(pts[:, 0] - xy[0], pts[:, 1] - xy[1])
    if dist.min() <= NODE_SNAP_M:
        return ids[int(dist.argmin())]
    p = Point(xy)
    best = min(((edge_line(G, u, v, k).distance(p), (u, v, k))
                for u, v, k in G.edges(keys=True) if u != v), key=lambda t: t[0])
    if best[0] > EDGE_SNAP_M:
        raise EditError(f"The point {lat:.5f}, {lon:.5f} is not on a road "
                        f"(nearest road is {best[0]:.0f} m away).")
    return split_edge(G, best[1], xy, splits)


def add_grade_road(G: nx.MultiDiGraph, a: int, b: int, kind: str, lanes: int) -> list[EdgeKey]:
    """Add a two-way flyover or underpass between two nodes (in place).

    The new road touches the network only at its two ends, so traffic on it
    never conflicts with the roads it passes over or under.

    Args:
        G: Graph to modify.
        a, b: End nodes.
        kind: "flyover" or "underpass".
        lanes: Lanes per direction.
    Returns:
        The two new directed edges.
    Raises:
        EditError: if the ends coincide or the road would be too short / long.
    """
    pa = (G.nodes[a]["x"], G.nodes[a]["y"])
    pb = (G.nodes[b]["x"], G.nodes[b]["y"])
    length = float(np.hypot(pa[0] - pb[0], pa[1] - pb[1]))
    if a == b or not MIN_ROAD_M <= length <= MAX_ROAD_M:
        raise EditError(f"A new {kind} must be {MIN_ROAD_M:.0f} m to {MAX_ROAD_M / 1000:.1f} km "
                        f"long; the two points are {length:.0f} m apart.")
    out = []
    for u, v, p, q in ((a, b, pa, pb), (b, a, pb, pa)):
        key = G.add_edge(u, v, highway="primary", name=f"New {kind}", added=True,
                         grade=GRADES[kind], n_lanes=int(lanes), maxspeed=NEW_ROAD_SPEED_KMH,
                         oneway=False, length=length, geometry=LineString([p, q]))
        out.append((u, v, key))
    return out


def _open_path(G: nx.MultiDiGraph, a: int, b: int) -> list[EdgeKey]:
    """Shortest directed path a -> b over existing open roads, as edges; [] if none.

    Closed roads and roads added by the scenario itself are not used: an
    extra lane widens the roads that are already there.
    """
    H = nx.DiGraph()
    for u, v, k, d in G.edges(keys=True, data=True):
        w = float(d.get("length", 1.0))
        usable = u != v and not d.get("closed") and not d.get("added")
        if usable and (not H.has_edge(u, v) or w < H[u][v]["w"]):
            H.add_edge(u, v, w=w, key=k)
    try:
        nodes = nx.shortest_path(H, a, b, weight="w")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return []
    return [(u, v, H[u][v]["key"]) for u, v in zip(nodes[:-1], nodes[1:], strict=True)]


def add_lane(G: nx.MultiDiGraph, a: int, b: int, both_directions: bool) -> list[EdgeKey]:
    """Add one lane to every road edge on the shortest path a -> b (in place).

    Args:
        G: Graph to modify.
        a, b: End nodes.
        both_directions: Also widen the way back, b -> a.
    Returns:
        The widened directed edges.
    Raises:
        EditError: if no road connects the points or nothing can be widened.
    """
    paths = [_open_path(G, a, b)] + ([_open_path(G, b, a)] if both_directions else [])
    if not any(paths):
        raise EditError("No road connects the two points" + ("" if both_directions else
                        " from the first to the second") + ", so no lane can be added.")
    done = []
    for u, v, k in dict.fromkeys(e for path in paths for e in path):
        d = G.edges[u, v, k]
        n = edge_lane_count(d)
        if n < MAX_EDGE_LANES:
            d["n_lanes"] = n + 1
            done.append((u, v, k))
    if not done:
        raise EditError(f"Every road between the two points already has {MAX_EDGE_LANES} lanes.")
    return done


def set_junction_control(G: nx.MultiDiGraph, node: int, control: str,
                         stop_s: float | None = None) -> None:
    """Make a junction a fixed-time signal or a circle (in place).

    Args:
        G: Graph to modify.
        node: Junction node id.
        control: "signal" or "circle".
        stop_s: Red time per approach for a signal; None keeps the run's value.
    Raises:
        EditError: if the node does not exist or is not a junction.
    """
    if node not in G or len(set(G.predecessors(node)) | set(G.successors(node))) < 3:
        raise EditError(f"Junction {node} does not exist in this area.")
    G.nodes[node]["control"] = control
    if control == "signal" and stop_s is not None:
        G.nodes[node]["signal_stop_s"] = float(stop_s)
    else:
        G.nodes[node].pop("signal_stop_s", None)


def expand(edge: EdgeKey, splits: dict) -> list[EdgeKey]:
    """Current edges that make up an edge named before the splits."""
    if edge not in splits:
        return [edge]
    return [e for part in splits[edge] for e in expand(part, splits)]
