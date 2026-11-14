"""Helpers for `network.compile_network`: geometry, boundary, radius, closures, labels.

Kept separate so each function stays small and testable.
"""

import json

import networkx as nx
import numpy as np
from pyproj import Transformer

BOUNDARY_BAND_M = 80.0
SNAP_DIST_M = 40.0
LANE_WIDTH_M = 3.2
LEG_CHORD_M = 30.0  # leg directions are measured over this distance from the node
MAX_RADIUS_M = 25.0
MIN_CIRCLE_RADIUS_M = 16.0


def edge_polyline(attrs: dict, xy_u: np.ndarray, xy_v: np.ndarray) -> np.ndarray:
    """Edge polyline in projected metres, snapped to its end nodes.

    Consolidation moves nodes to cluster centroids, so the stored geometry
    may not start/end exactly at the nodes; we snap or extend it.

    Args:
        attrs: Edge attributes (optional shapely `geometry`).
        xy_u: Start node coordinates.
        xy_v: End node coordinates.
    Returns:
        (K, 2) polyline with K >= 2 and no zero-length segments.
    """
    geom = attrs.get("geometry")
    pts = np.asarray(geom.coords, dtype=float)[:, :2] if geom is not None else np.empty((0, 2))
    if len(pts) < 2:
        pts = np.vstack([xy_u, xy_v])
    if np.linalg.norm(pts[0] - xy_u) < SNAP_DIST_M:
        pts[0] = xy_u
    else:
        pts = np.vstack([xy_u, pts])
    if np.linalg.norm(pts[-1] - xy_v) < SNAP_DIST_M:
        pts[-1] = xy_v
    else:
        pts = np.vstack([pts, xy_v])
    keep = np.concatenate([[True], np.linalg.norm(np.diff(pts, axis=0), axis=1) > 1e-3])
    pts = pts[keep]
    return pts if len(pts) >= 2 else np.vstack([xy_u, xy_v + 1e-3])


def boundary_nodes(G: nx.MultiDiGraph, xy: np.ndarray, nbrs: np.ndarray) -> np.ndarray:
    """Entry/exit nodes: dead ends on (or outside) the area's bounding box.

    `truncate_by_edge` keeps the first node outside the bbox for roads that
    cross it; those nodes have exactly one neighbour.

    Args:
        G: Projected graph with graph attr `bbox` = JSON [s, w, n, e] (degrees).
        xy: (N, 2) node coordinates.
        nbrs: (N,) number of distinct neighbours per node.
    Returns:
        (N,) bool mask.
    """
    dead_end = nbrs == 1
    if "bbox" not in G.graph:
        return dead_end
    s, w, n, e = json.loads(G.graph["bbox"])
    tf = Transformer.from_crs("EPSG:4326", G.graph["crs"], always_xy=True)
    xs, ys = tf.transform([w, e, w, e], [s, s, n, n])
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    dist_in = np.minimum.reduce([xy[:, 0] - x0, x1 - xy[:, 0], xy[:, 1] - y0, y1 - xy[:, 1]])
    near_edge = dist_in < BOUNDARY_BAND_M
    mask = dead_end & near_edge
    return mask if mask.sum() >= 2 else dead_end


def _legs(node: int, frm: np.ndarray, to: np.ndarray, pts: list[np.ndarray],
          lanes: np.ndarray, edges: list[int]) -> list[tuple[np.ndarray, float]]:
    """Road legs of one node: (unit direction away from the node, half width in m).

    Both directions of a two-way road form one leg (the carriageways lie
    either side of the shared centre line).
    """
    by_other: dict[int, list[int]] = {}
    for e in edges:
        by_other.setdefault(int(to[e] if frm[e] == node else frm[e]), []).append(e)
    legs = []
    for group in by_other.values():
        e = group[0]
        p = pts[e] if frm[e] == node else pts[e][::-1]
        seg = np.linalg.norm(np.diff(p, axis=0), axis=1)
        far = p[min(int(np.searchsorted(np.cumsum(seg), LEG_CHORD_M)) + 1, len(p) - 1)]
        d = far - p[0]
        two_way = len({(int(frm[g]), int(to[g])) for g in group}) > 1
        width = max(int(lanes[g]) for g in group) * LANE_WIDTH_M
        legs.append((d / max(float(np.linalg.norm(d)), 1e-9), width if two_way else width / 2))
    return legs


def junction_radius(frm: np.ndarray, to: np.ndarray, lanes: np.ndarray, nbrs: np.ndarray,
                    boundary: np.ndarray, n_nodes: int,
                    pts: list[np.ndarray] | None = None,
                    circle: np.ndarray | None = None) -> np.ndarray:
    """Physical half-size of each junction box in metres.

    Edges are trimmed by this radius; turn paths span the trimmed region.
    The radius grows with the widest approach and, when `pts` is given, is
    large enough that neighbouring legs no longer overlap where they are cut:
    two legs with half widths a, b meeting at angle t are clear of each other
    at distance (a + b) / (2 sin(t / 2)). Zero for pass-through and boundary nodes.

    Args:
        frm, to: (E,) node index per edge.
        lanes: (E,) lanes per direction.
        nbrs: (N,) distinct neighbours per node.
        boundary: (N,) entry/exit node mask.
        n_nodes: Number of nodes.
        pts: Untrimmed edge polylines (optional).
        circle: (N,) mask of roundabout junctions, which get at least
            MIN_CIRCLE_RADIUS_M so a ring fits around the island.
    """
    max_lanes = np.zeros(n_nodes)
    np.maximum.at(max_lanes, frm, lanes)
    np.maximum.at(max_lanes, to, lanes)
    r = np.clip(3.0 + 1.6 * max_lanes, 6.0, 14.0)
    r[(nbrs < 3) | boundary] = 0.0
    if circle is not None:
        r[circle] = np.maximum(r[circle], MIN_CIRCLE_RADIUS_M)
    if pts is None:
        return r
    at_node: dict[int, list[int]] = {}
    for e in range(len(frm)):
        at_node.setdefault(int(frm[e]), []).append(e)
        at_node.setdefault(int(to[e]), []).append(e)
    for node in np.flatnonzero(r > 0):
        legs = _legs(int(node), frm, to, pts, lanes, at_node[int(node)])
        for i, (d1, w1) in enumerate(legs):
            for d2, w2 in legs[i + 1:]:
                half = np.sqrt(max((1.0 - float(np.dot(d1, d2))) / 2.0, 1e-4))  # sin(t/2)
                r[node] = max(r[node], min((w1 + w2) / (2.0 * half), MAX_RADIUS_M))
    return r


def closures_of(rows: list[tuple], pts: list[np.ndarray], attrs: dict) -> tuple:
    """Per-lane closure intervals from edge attr `lane_closures`.

    Each entry: {"lanes": [k, ...] | None (all), "f0": 0..1, "f1": 0..1}.
    Closing all lanes of an edge anywhere marks the edge closed (mutates
    `attrs["closed"]`) since nothing can pass.

    Returns:
        (link, lane, s0, s1) arrays.
    """
    out = ([], [], [], [])
    for e, (_, _, _, d) in enumerate(rows):
        spec = d.get("lane_closures")
        if not spec or attrs["closed"][e]:
            continue
        spec = json.loads(spec) if isinstance(spec, str) else spec
        length = float(np.linalg.norm(np.diff(pts[e], axis=0), axis=1).sum())
        n = int(attrs["lanes"][e])
        for c in spec:
            lanes = [k for k in (c.get("lanes") or range(n)) if 0 <= k < n]
            if len(lanes) == n:
                attrs["closed"][e] = True
            for k in lanes:
                vals = (e, k, float(c.get("f0", 0.0)) * length, float(c.get("f1", 1.0)) * length)
                for lst, v in zip(out, vals, strict=True):
                    lst.append(v)
    return (np.asarray(out[0], np.int32), np.asarray(out[1], np.int32),
            np.asarray(out[2], float), np.asarray(out[3], float))


def labels_for(rows: list[tuple], xy: np.ndarray, kind: np.ndarray, boundary_kind: int,
               junction_kinds: tuple, node_ids: list | None = None,
               base_labels: dict | None = None) -> tuple[list[str], list[str]]:
    """Human labels: junctions J#, boundary B#, other nodes N#; roads R# per node pair.

    Nodes are numbered in reading order (north to south, west to east) so the
    labels are stable for a given area. A scenario passes the baseline's
    labels: a node keeps its label while its class (J / B / N) is unchanged,
    and nodes that are new or changed class get the next free numbers.

    Args:
        rows: Edge rows (u_i, v_i, key, attrs).
        xy: (N, 2) node coordinates.
        kind: (N,) node kinds.
        boundary_kind: Kind value of boundary nodes.
        junction_kinds: Kind values that count as junctions.
        node_ids: Graph node id per index (needed with `base_labels`).
        base_labels: node id -> label in the baseline network.
    Returns:
        (node_label list, edge_road list).
    """
    base_labels = base_labels or {}
    order = np.lexsort((xy[:, 0], -xy[:, 1]))
    counters = {"J": 0, "B": 0, "N": 0}
    for label in base_labels.values():
        counters[label[0]] = max(counters[label[0]], int(label[1:]))
    node_label = [""] * len(xy)
    for i in order:
        prefix = "B" if kind[i] == boundary_kind else ("J" if kind[i] in junction_kinds else "N")
        old = base_labels.get(node_ids[i]) if node_ids is not None else None
        if old and old[0] == prefix:
            node_label[i] = old
            continue
        counters[prefix] += 1
        node_label[i] = f"{prefix}{counters[prefix]}"
    road_of: dict[frozenset, str] = {}
    edge_road = []
    for u, v, _, _ in rows:
        key = frozenset((u, v))
        if key not in road_of:
            road_of[key] = f"R{len(road_of) + 1}"
        edge_road.append(road_of[key])
    return node_label, edge_road
