"""Helpers for `network.compile_network`: geometry, boundary, radius, closures, labels.

Kept separate so each function stays small and testable.
"""

import json

import networkx as nx
import numpy as np
from pyproj import Transformer

BOUNDARY_BAND_M = 80.0
SNAP_DIST_M = 40.0


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


def junction_radius(frm: np.ndarray, to: np.ndarray, lanes: np.ndarray, nbrs: np.ndarray,
                    boundary: np.ndarray, n_nodes: int) -> np.ndarray:
    """Physical half-size of each junction box in metres.

    Edges are trimmed by this radius; turn paths span the trimmed region.
    Grows with the widest approach. Zero for pass-through and boundary nodes.
    """
    max_lanes = np.zeros(n_nodes)
    np.maximum.at(max_lanes, frm, lanes)
    np.maximum.at(max_lanes, to, lanes)
    r = np.clip(3.0 + 1.6 * max_lanes, 6.0, 14.0)
    r[(nbrs < 3) | boundary] = 0.0
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
               junction_kinds: tuple) -> tuple[list[str], list[str]]:
    """Human labels: junctions J#, boundary B#, other nodes N#; roads R# per node pair.

    Nodes are numbered in reading order (north to south, west to east) so the
    labels are stable for a given area.

    Returns:
        (node_label list, edge_road list).
    """
    order = np.lexsort((xy[:, 0], -xy[:, 1]))
    counters = {"J": 0, "B": 0, "N": 0}
    node_label = [""] * len(xy)
    for i in order:
        prefix = "B" if kind[i] == boundary_kind else ("J" if kind[i] in junction_kinds else "N")
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
