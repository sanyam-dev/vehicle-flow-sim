"""Junction movements (turns) and their conflict matrix.

A movement is one allowed (in-edge -> out-edge) path through a node. Its
geometry is a quadratic Bezier from the trimmed end of the in-edge to the
trimmed start of the out-edge, offset to the driving side. Two movements
at the same node conflict if their paths cross or they merge into the
same out-edge. Crossing conflicts are resolved by junction control
(`junctions.admit`); merges are resolved by car-following in a shared
queue (`leaders.junction_queues`), so merging traffic zips together.
Movements sharing an in-edge never conflict (they queue in lanes instead).
Grade-separated roads never share a node, so they can never produce a
conflict here.
"""

from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import LineString

from app.sim.geometry import (
    LANE_WIDTH_M,
    bezier,
    end_heading,
    point_at,
    polyline_length,
    side_normal,
)

STRAIGHT, NEAR_TURN, FAR_TURN, UTURN = 0, 1, 2, 3
# Approach headings are measured over this far from the junction centre:
# consolidated junctions distort the last tens of metres of each road.
TURN_CHORD_M = 100.0
STRAIGHT_MAX_DEG, UTURN_MIN_DEG = 45.0, 160.0
TURN_SPEED_MS = {NEAR_TURN: 5.0, FAR_TURN: 7.0, UTURN: 3.0}
ANY_LANE, LAST_LANE = -1, -2
MAX_TURN_LANES = 2


@dataclass
class EdgeShape:
    """Per-edge inputs needed to build movements.

    Attributes:
        frm, to: (E,) node indices.
        pts: Trimmed polylines, one (K, 2) array per edge.
        lanes: (E,) lanes per direction.
        vmax: (E,) speed limits (m/s).
        two_way: (E,) True if the reverse edge exists (offset to driving side).
        usable: (E,) False for fully closed edges (no movements built).
    """

    frm: np.ndarray
    to: np.ndarray
    pts: list[np.ndarray]
    lanes: np.ndarray
    vmax: np.ndarray
    two_way: np.ndarray
    usable: np.ndarray


@dataclass
class Movements:
    """All movements of a network (index m; link index = n_edges + m)."""

    m_in: list[int] = field(default_factory=list)
    m_out: list[int] = field(default_factory=list)
    node: list[int] = field(default_factory=list)
    turn: list[int] = field(default_factory=list)
    pref_lane: list[int] = field(default_factory=list)
    lanes: list[int] = field(default_factory=list)
    length: list[float] = field(default_factory=list)
    vmax: list[float] = field(default_factory=list)
    paths: list[np.ndarray] = field(default_factory=list)
    conflicts: list[np.ndarray] = field(default_factory=list)
    # conflict_s[a][j]: distance along movement conflicts[a][j] to the conflict point
    conflict_s: list[np.ndarray] = field(default_factory=list)
    # conflict_own[a][j]: distance along movement a itself to that conflict point
    conflict_own: list[np.ndarray] = field(default_factory=list)
    # crossing[a][j]: True where the paths cross, False for a pure merge into one out-edge
    crossing: list[np.ndarray] = field(default_factory=list)
    # nodes where two paths cross (not just merge): these need a control
    crossing_nodes: set[int] = field(default_factory=set)


def _turn_class(d_in: np.ndarray, d_out: np.ndarray, driving_side: str) -> int:
    """Classify a turn from the in/out unit directions.

    Args:
        d_in: Heading at the end of the in-edge.
        d_out: Heading at the start of the out-edge.
        driving_side: "left" or "right" (decides which turn is near-side).
    Returns:
        STRAIGHT, NEAR_TURN, FAR_TURN or UTURN.
    """
    cross = d_in[0] * d_out[1] - d_in[1] * d_out[0]
    angle = np.degrees(np.arctan2(cross, float(np.dot(d_in, d_out))))
    if abs(angle) < STRAIGHT_MAX_DEG:
        return STRAIGHT
    if abs(angle) > UTURN_MIN_DEG:
        return UTURN
    is_left = angle > 0
    return NEAR_TURN if is_left == (driving_side == "left") else FAR_TURN


def _carriageway_offset(edges: EdgeShape, e: int) -> float:
    """Lateral offset (m) of a directed carriageway's centre from the polyline."""
    return float(edges.lanes[e]) * LANE_WIDTH_M / 2 if edges.two_way[e] else 0.0


def _headings(edges: EdgeShape, e_in: int, e_out: int) -> tuple[np.ndarray, np.ndarray]:
    """Unit headings at the end of the in-edge and the start of the out-edge."""
    return end_heading(edges.pts[e_in], at_end=True), end_heading(edges.pts[e_out], at_end=False)


def _approach_headings(edges: EdgeShape, e_in: int, e_out: int,
                       node_xy: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Headings of the approach and exit roads as seen from the junction centre.

    Uses chords from the junction node to a point TURN_CHORD_M along each road
    (or its far end), which ignores the distorted geometry inside large
    consolidated junctions. Used only to classify the turn.
    """
    p_in, p_out = edges.pts[e_in], edges.pts[e_out]
    a, _ = point_at(p_in, max(polyline_length(p_in) - TURN_CHORD_M, 0.0))
    b, _ = point_at(p_out, min(TURN_CHORD_M, polyline_length(p_out)))
    d_in, d_out = node_xy - a, b - node_xy
    return (d_in / max(np.linalg.norm(d_in), 1e-9), d_out / max(np.linalg.norm(d_out), 1e-9))


def _path(edges: EdgeShape, e_in: int, e_out: int, node_xy: np.ndarray,
          side: str) -> np.ndarray:
    """Bezier turn path from in-edge end to out-edge start, offset to the driving side."""
    p_in, p_out = edges.pts[e_in], edges.pts[e_out]
    d_in, d_out = _headings(edges, e_in, e_out)
    n_in = side_normal(d_in, side) * _carriageway_offset(edges, e_in)
    n_out = side_normal(d_out, side) * _carriageway_offset(edges, e_out)
    p0, p1 = p_in[-1] + n_in, p_out[0] + n_out
    ctrl = node_xy + (n_in + n_out) / 2
    return bezier(p0, ctrl, p1)


def _add_movement(mv: Movements, edges: EdgeShape, e_in: int, e_out: int,
                  node: int, node_xy: np.ndarray, side: str) -> None:
    """Append one movement (geometry, turn class, speed, preferred lane)."""
    path = _path(edges, e_in, e_out, node_xy, side)
    turn = _turn_class(*_approach_headings(edges, e_in, e_out, node_xy), side)
    if edges.to[e_out] == edges.frm[e_in]:
        turn = UTURN  # leads straight back to where the vehicle came from
    vmax = min(edges.vmax[e_in], edges.vmax[e_out], TURN_SPEED_MS.get(turn, 99.0))
    pref = {STRAIGHT: ANY_LANE, NEAR_TURN: 0}.get(turn, LAST_LANE)
    width = int(min(edges.lanes[e_in], edges.lanes[e_out]))
    lanes = {STRAIGHT: width, UTURN: 1}.get(turn, min(width, MAX_TURN_LANES))
    mv.m_in.append(e_in)
    mv.m_out.append(e_out)
    mv.node.append(node)
    mv.turn.append(turn)
    mv.pref_lane.append(pref)
    mv.lanes.append(max(lanes, 1))
    mv.length.append(max(polyline_length(path), 1.0))
    mv.vmax.append(float(vmax))
    mv.paths.append(path)


def _conflict_point(line_a: LineString, line_b: LineString, same_out: bool) -> float | None:
    """Distance along `line_b` that a vehicle on it must clear before `a` may enter.

    Crossing paths: the furthest crossing point along b. Paths merging into the
    same out-edge: the end of b. None if the movements do not conflict.
    """
    if same_out:
        return line_b.length
    if not line_a.intersects(line_b):
        return None
    inter = line_a.intersection(line_b)
    parts = getattr(inter, "geoms", [inter])
    return max(line_b.project(p) for g in parts for p in [g.representative_point()])


def _node_conflicts(mv: Movements, ids: list[int]) -> None:
    """Fill the conflict lists (and conflict points) for the movements `ids` of one node.

    Args:
        mv: Movements under construction (paths already set).
        ids: Global movement indices belonging to this node.
    """
    lines = {m: LineString(mv.paths[m]) for m in ids}
    for a in ids:
        hits, at, own, cross = [], [], [], []
        for b in ids:
            if a == b or mv.m_in[a] == mv.m_in[b]:
                continue
            merge = mv.m_out[a] == mv.m_out[b]
            s = _conflict_point(lines[a], lines[b], merge)
            if s is None:
                continue
            s_own = _conflict_point(lines[b], lines[a], merge)
            hits.append(b)
            at.append(s * mv.length[b] / max(lines[b].length, 1e-9))
            own.append((s if s_own is None else s_own) * mv.length[a]
                       / max(lines[a].length, 1e-9))
            cross.append(not merge)
            if not merge:
                mv.crossing_nodes.add(mv.node[a])
        mv.conflicts[a] = np.asarray(hits, dtype=np.int32)
        mv.conflict_s[a] = np.asarray(at, dtype=float)
        mv.conflict_own[a] = np.asarray(own, dtype=float)
        mv.crossing[a] = np.asarray(cross, dtype=bool)


def build_movements(edges: EdgeShape, node_xy: np.ndarray, boundary: np.ndarray,
                    side: str) -> Movements:
    """Create every movement and the per-node conflict matrix.

    U-turns are only allowed at nodes where no other exit exists.
    Boundary nodes have no movements (vehicles leave the network there).

    Args:
        edges: Per-edge shapes and attributes.
        node_xy: (N, 2) node coordinates.
        boundary: (N,) bool, True for entry/exit nodes.
        side: Driving side, "left" or "right".
    Returns:
        Movements with conflicts filled.
    """
    mv = Movements()
    ins_by: dict[int, list[int]] = {}
    outs_by: dict[int, list[int]] = {}
    for e in np.flatnonzero(edges.usable):
        ins_by.setdefault(int(edges.to[e]), []).append(int(e))
        outs_by.setdefault(int(edges.frm[e]), []).append(int(e))
    for node in range(len(node_xy)):
        if boundary[node]:
            continue
        ins, outs = ins_by.get(node, []), outs_by.get(node, [])
        start = len(mv.m_in)
        for e_in in ins:
            non_u = [o for o in outs if edges.to[o] != edges.frm[e_in]]
            for e_out in (non_u or outs):
                _add_movement(mv, edges, e_in, e_out, node, node_xy[node], side)
        mv.conflicts.extend([np.zeros(0, np.int32)] * (len(mv.m_in) - start))
        mv.conflict_s.extend([np.zeros(0)] * (len(mv.m_in) - start))
        mv.conflict_own.extend([np.zeros(0)] * (len(mv.m_in) - start))
        mv.crossing.extend([np.zeros(0, bool)] * (len(mv.m_in) - start))
        _node_conflicts(mv, list(range(start, len(mv.m_in))))
    return mv
