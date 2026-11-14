"""Compile an osmnx graph into flat numpy arrays for the engine.

Links are indexed 0..E-1 for road edges and E..E+M-1 for junction
movements, so a vehicle is always "on link l, lane k, at distance s".
Edge ids are stable between a baseline graph and a scenario graph built
from it: edges are ordered by (added, u, v, key) and closed edges are
kept (flagged), so scenario edits only append.
"""

from dataclasses import dataclass, field

import networkx as nx
import numpy as np
from pyproj import Transformer

from app.geo.grade import edge_grade, ramp_kind
from app.geo.graph_builder import graph_signals
from app.geo.lanes import (
    base_class,
    class_rank,
    first,
    is_link,
    lanes_per_direction,
    speed_limit_ms,
)
from app.sim.geometry import GeomTable, drop_end_hooks, polyline_length, substring
from app.sim.movements import LAST_LANE, EdgeShape, Movements, build_movements
from app.sim.network_parts import (
    boundary_nodes,
    closures_of,
    edge_polyline,
    junction_radius,
    labels_for,
)

PASS, PRIORITY, SIGNAL, BOUNDARY = 0, 1, 2, 3
SIGNAL_MATCH_RADIUS_M = 30.0
MIN_EDGE_LEN_M = 4.0


@dataclass
class Network:
    """Flat, engine-ready road network. See module docstring for link indexing."""

    driving_side: str
    crs: str
    area_id: str
    node_id: np.ndarray
    node_xy: np.ndarray
    node_kind: np.ndarray
    node_label: list[str]
    node_ramp: list[str | None]
    n_edges: int
    edge_uvk: list[tuple]
    edge_name: list[str]
    edge_road: list[str]
    edge_highway: list[str]
    edge_closed: np.ndarray
    edge_added: np.ndarray
    link_len: np.ndarray
    link_vmax: np.ndarray
    link_lanes: np.ndarray
    link_from: np.ndarray
    link_to: np.ndarray
    link_grade: np.ndarray
    link_rank: np.ndarray
    mv_in: np.ndarray
    mv_out: np.ndarray
    mv_node: np.ndarray
    mv_turn: np.ndarray
    mv_pref: np.ndarray
    mv_conflicts: list[np.ndarray]
    mv_conflict_s: list[np.ndarray]
    mv_conflict_own: list[np.ndarray]
    mv_cross: list[np.ndarray]
    mv_cross_s: list[np.ndarray]
    mv_cross_own: list[np.ndarray]
    mv_index: dict[tuple[int, int], int]
    geom: GeomTable
    entries: np.ndarray
    exits: np.ndarray
    clo_link: np.ndarray
    clo_lane: np.ndarray
    clo_s0: np.ndarray
    clo_s1: np.ndarray
    to_lonlat: Transformer = field(repr=False, default=None)

    @property
    def n_links(self) -> int:
        """Number of links (edges + movements)."""
        return len(self.link_len)

    @property
    def n_movements(self) -> int:
        """Number of junction movements."""
        return len(self.mv_in)

    def movement_lane(self, mv_link: np.ndarray, lane_in: np.ndarray) -> np.ndarray:
        """Lane taken inside a movement by a vehicle arriving in `lane_in`.

        Near turns and straight movements keep kerb-side alignment; far turns
        align with the far side of the approach.

        Args:
            mv_link: (n,) movement link ids (>= n_edges).
            lane_in: (n,) lanes on the in-edge.
        Returns:
            (n,) lane index on the movement.
        """
        m = mv_link - self.n_edges
        width = self.link_lanes[mv_link].astype(np.int64)
        lane = lane_in.astype(np.int64)
        far = self.mv_pref[m] == LAST_LANE
        lane = np.where(far, lane - (self.link_lanes[self.mv_in[m]] - width), lane)
        return np.clip(lane, 0, width - 1)

    def lane_blocked(self, link: np.ndarray, lane: np.ndarray, s0: np.ndarray,
                     s1: np.ndarray | None = None) -> np.ndarray:
        """Whether a lane is closed anywhere in [s0, s1] on the given links.

        Args:
            link: (n,) link indices.
            lane: (n,) lane indices.
            s0: (n,) interval start (m).
            s1: (n,) interval end (m); defaults to s0 (point query).
        Returns:
            (n,) bool.
        """
        s1 = s0 if s1 is None else s1
        out = np.zeros(len(link), dtype=bool)
        for cl, ck, a, b in zip(self.clo_link, self.clo_lane, self.clo_s0, self.clo_s1,
                                strict=True):
            out |= (link == cl) & (lane == ck) & (s1 >= a) & (s0 <= b)
        return out


def _collect_edges(G: nx.MultiDiGraph, idx: dict) -> list[tuple]:
    """Directed edges (u_i, v_i, (u, v, key), attrs) sorted for stable ids.

    Self-loops (artefacts of intersection consolidation) are dropped.
    """
    rows = [(idx[u], idx[v], (u, v, k), d) for u, v, k, d in G.edges(keys=True, data=True)
            if u != v]
    rows.sort(key=lambda r: (bool(r[3].get("added", False)), r[0], r[1], str(r[2][2])))
    return rows


def _edge_attrs(rows: list[tuple], pts: list[np.ndarray]) -> dict[str, np.ndarray]:
    """Lanes, speed, grade, rank and flags for every edge row.

    Args:
        rows: Output of `_collect_edges`.
        pts: Full (untrimmed) polylines, same order.
    Returns:
        Dict of (E,) arrays.
    """
    pairs = {(r[0], r[1]) for r in rows}
    out = {k: [] for k in ("lanes", "vmax", "grade", "rank", "two_way", "closed", "added")}
    for (u, v, _, d), _p in zip(rows, pts, strict=True):
        oneway = str(first(d.get("oneway"))).lower() in ("true", "yes", "1")
        rev = str(first(d.get("reversed"))).lower() in ("true", "1")
        n = int(d["n_lanes"]) if d.get("n_lanes") else lanes_per_direction(d, oneway, rev)
        rank = class_rank(d.get("highway")) - (1 if is_link(d.get("highway")) else 0)
        out["lanes"].append(n)
        out["vmax"].append(speed_limit_ms(d))
        out["grade"].append(edge_grade(d))
        out["rank"].append(rank)
        out["two_way"].append((v, u) in pairs)
        out["closed"].append(bool(d.get("closed", False)))
        out["added"].append(bool(d.get("added", False)))
    return {k: np.asarray(v) for k, v in out.items()}


def _trim(pts: list[np.ndarray], frm: np.ndarray, to: np.ndarray,
          radius: np.ndarray) -> list[np.ndarray]:
    """Cut each edge back by the junction radius at both ends.

    The removed parts are covered by the movement (turn) paths.
    """
    out = []
    for p, u, v in zip(pts, frm, to, strict=True):
        length = polyline_length(p)
        ru, rv = radius[u], radius[v]
        if length - ru - rv < MIN_EDGE_LEN_M and ru + rv > 0:
            scale = max(length - MIN_EDGE_LEN_M, 0.0) / (ru + rv)
            ru, rv = ru * scale, rv * scale
        out.append(drop_end_hooks(substring(p, ru, length - rv)))
    return out


def _node_kinds(G: nx.MultiDiGraph, xy: np.ndarray, nbrs: np.ndarray,
                boundary: np.ndarray) -> np.ndarray:
    """PASS / PRIORITY / SIGNAL / BOUNDARY for every node.

    A junction (>= 3 distinct neighbours) is signalised when an OSM
    traffic-signal point lies within SIGNAL_MATCH_RADIUS_M.
    """
    kind = np.where(nbrs >= 3, PRIORITY, PASS).astype(np.int8)
    sig = np.asarray(graph_signals(G)).reshape(-1, 2)
    if len(sig):
        d = np.linalg.norm(xy[:, None, :] - sig[None, :, :], axis=2).min(axis=1)
        kind[(d <= SIGNAL_MATCH_RADIUS_M) & (nbrs >= 3)] = SIGNAL
    for i, n in enumerate(sorted(G.nodes)):
        override = G.nodes[n].get("signal")  # set by the scenario modifier
        if override is not None and nbrs[i] >= 3:
            kind[i] = SIGNAL if str(override).lower() in ("true", "1", "yes") else PRIORITY
    kind[boundary] = BOUNDARY
    return kind


def _ramps(rows: list[tuple], n_nodes: int) -> list[str | None]:
    """Merge / diverge classification per node from edge `highway` tags."""
    ins = [[] for _ in range(n_nodes)]
    outs = [[] for _ in range(n_nodes)]
    for u, v, _, d in rows:
        outs[u].append(d)
        ins[v].append(d)
    return [ramp_kind(i, o) for i, o in zip(ins, outs, strict=True)]


def compile_network(G: nx.MultiDiGraph) -> Network:
    """Compile a projected, consolidated osmnx graph into a Network.

    Args:
        G: Graph from `graph_builder.load_or_build`, optionally modified by
           the scenario modifier (edge attrs `closed`, `lane_closures`,
           `added`, `n_lanes`, `grade`).
    Returns:
        Engine-ready Network.
    """
    side = G.graph.get("driving_side", "left")
    node_ids = np.asarray(sorted(G.nodes))
    idx = {n: i for i, n in enumerate(node_ids)}
    xy = np.asarray([[G.nodes[n]["x"], G.nodes[n]["y"]] for n in node_ids], dtype=float)
    rows = _collect_edges(G, idx)
    frm = np.asarray([r[0] for r in rows], dtype=np.int32)
    to = np.asarray([r[1] for r in rows], dtype=np.int32)
    full_pts = [edge_polyline(r[3], xy[r[0]], xy[r[1]]) for r in rows]
    attrs = _edge_attrs(rows, full_pts)
    nbrs = np.asarray([len(set(G.predecessors(n)) | set(G.successors(n))) for n in node_ids])
    boundary = boundary_nodes(G, xy, nbrs)
    radius = junction_radius(frm, to, attrs["lanes"], nbrs, boundary, len(node_ids), full_pts)
    pts = _trim(full_pts, frm, to, radius)
    clo = closures_of(rows, pts, attrs)
    shape = EdgeShape(frm, to, pts, attrs["lanes"], attrs["vmax"], attrs["two_way"],
                      ~attrs["closed"])
    mv = build_movements(shape, xy, boundary, side)
    return _assemble(G, node_ids, xy, rows, pts, attrs, mv, boundary, nbrs, clo, side)


def _assemble(G, node_ids, xy, rows, pts, attrs, mv: Movements, boundary, nbrs, clo,
              side) -> Network:
    """Build the Network dataclass from the compiled parts (see compile_network)."""
    E, M = len(rows), len(mv.m_in)
    frm = np.asarray([r[0] for r in rows] + mv.node, dtype=np.int32)
    to = np.asarray([r[1] for r in rows] + mv.node, dtype=np.int32)
    names = [str(first(r[3].get("name")) or "") for r in rows]
    kind = _node_kinds(G, xy, nbrs, boundary)
    node_label, edge_road = labels_for(rows, xy, kind, BOUNDARY, (PRIORITY, SIGNAL))
    usable = ~attrs["closed"]
    has_out = np.bincount(frm[:E][usable], minlength=len(xy)) > 0
    has_in = np.bincount(to[:E][usable], minlength=len(xy)) > 0
    return Network(
        driving_side=side, crs=str(G.graph["crs"]), area_id=str(G.graph.get("area_id", "")),
        node_id=node_ids, node_xy=xy, node_kind=kind, node_label=node_label,
        node_ramp=_ramps(rows, len(xy)), n_edges=E,
        edge_uvk=[r[2] for r in rows], edge_name=names, edge_road=edge_road,
        edge_highway=[base_class(r[3].get("highway")) for r in rows],
        edge_closed=attrs["closed"], edge_added=attrs["added"],
        link_len=np.asarray([polyline_length(p) for p in pts] + mv.length),
        link_vmax=np.concatenate([attrs["vmax"], np.asarray(mv.vmax)]),
        link_lanes=np.concatenate([attrs["lanes"], np.asarray(mv.lanes, int)]).astype(np.int8),
        link_from=frm, link_to=to,
        link_grade=np.concatenate([attrs["grade"], np.zeros(M, int)]).astype(np.int8),
        link_rank=np.concatenate([attrs["rank"], np.zeros(M, int)]).astype(np.int8),
        mv_in=np.asarray(mv.m_in, np.int32), mv_out=np.asarray(mv.m_out, np.int32),
        mv_node=np.asarray(mv.node, np.int32), mv_turn=np.asarray(mv.turn, np.int8),
        mv_pref=np.asarray(mv.pref_lane, np.int8), mv_conflicts=mv.conflicts,
        mv_conflict_s=mv.conflict_s, mv_conflict_own=mv.conflict_own,
        mv_cross=[c[x] for c, x in zip(mv.conflicts, mv.crossing, strict=True)],
        mv_cross_s=[c[x] for c, x in zip(mv.conflict_s, mv.crossing, strict=True)],
        mv_cross_own=[c[x] for c, x in zip(mv.conflict_own, mv.crossing, strict=True)],
        mv_index={(a, b): i for i, (a, b) in enumerate(zip(mv.m_in, mv.m_out, strict=True))},
        geom=GeomTable(pts + mv.paths),
        entries=np.flatnonzero(boundary & has_out), exits=np.flatnonzero(boundary & has_in),
        clo_link=clo[0], clo_lane=clo[1], clo_s0=clo[2], clo_s1=clo[3],
        to_lonlat=Transformer.from_crs(G.graph["crs"], "EPSG:4326", always_xy=True),
    )
