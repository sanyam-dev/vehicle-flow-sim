"""Route cache (capacity-aware MSA assignment) and per-network route tables.

`RouteCache` stores, per entry->exit pair, a *mix* of edge paths with
demand shares. Edge ids are stable between baseline and scenario networks
(see network.py), so a scenario cache is a copy of the baseline cache where
only the pairs touching a closed edge are re-routed.

`RouteTable` turns those edge paths into link sequences
(edge, movement, edge, movement, ..., edge) as flat arrays for the engine,
and draws one path per vehicle from its OD pair's mix.
"""

import copy
from dataclasses import dataclass

import networkx as nx
import numpy as np

from app.sim.network import PRIORITY, ROUNDABOUT, SIGNAL, Network

NODE_PENALTY_S = {SIGNAL: 20.0, PRIORITY: 4.0, ROUNDABOUT: 8.0}
# Perceived cost per road class: through traffic prefers main roads and only
# rat-runs through minor streets when they save a lot of time.
CLASS_COST = {"motorway": 1.0, "trunk": 1.0, "primary": 1.0, "secondary": 1.1,
              "tertiary": 1.2, "unclassified": 1.5, "residential": 1.6,
              "service": 2.0, "living_street": 2.5}

# Capacity-aware assignment (method of successive averages, MSA): routes are
# re-chosen a few times with BPR-congested edge times so demand spreads over
# parallel roads instead of funnelling onto one narrow shortcut.
ASSIGN_ITERATIONS = 10
# Planning capacity per lane (veh/s) by road class: minor streets are narrow,
# have parked vehicles and pedestrians.
LANE_CAPACITY_VPS = {"motorway": 0.55, "trunk": 0.5, "primary": 0.5, "secondary": 0.45,
                     "tertiary": 0.4, "unclassified": 0.3, "residential": 0.2,
                     "service": 0.15, "living_street": 0.1}
# Share of time the end junction lets an approach through.
SIGNAL_SHARE = 0.45
PRIORITY_MAJOR_SHARE, PRIORITY_MINOR_SHARE = 0.9, 0.4
# BPR alpha = 1 keeps under-used roads near free-flow time but makes
# overloaded ones (v/c > 1) quickly unattractive.
BPR_ALPHA, BPR_BETA = 1.0, 4.0


def _junction_share(net: Network) -> np.ndarray:
    """(E,) share of time each edge's end junction serves it.

    At a priority junction an edge ranked below the best in-edge is a minor
    arm that only enters in gaps.
    """
    E = net.n_edges
    to, rank = net.link_to[:E], net.link_rank[:E].astype(int)
    best = np.full(len(net.node_xy), -99)
    np.maximum.at(best, to, rank)
    kind = net.node_kind[to]
    share = np.ones(E)
    share[kind == SIGNAL] = SIGNAL_SHARE
    share[kind == ROUNDABOUT] = PRIORITY_MINOR_SHARE
    prio = kind == PRIORITY
    share[prio] = np.where(rank[prio] < best[to[prio]], PRIORITY_MINOR_SHARE,
                           PRIORITY_MAJOR_SHARE)
    return share


def edge_capacity(net: Network) -> np.ndarray:
    """Rough capacity (veh/s) of each road edge, limited by its end junction.

    Only used to steer route choice; simulated capacity still emerges from
    IDM/MOBIL and junction control.

    Args:
        net: Compiled network.
    Returns:
        (E,) capacities.
    """
    E = net.n_edges
    per_lane = np.array([LANE_CAPACITY_VPS.get(h, 0.3) for h in net.edge_highway[:E]])
    return np.maximum(net.link_lanes[:E], 1) * per_lane * _junction_share(net)


def bpr_factor(flow: np.ndarray, cap: np.ndarray) -> np.ndarray:
    """BPR travel-time multiplier 1 + alpha (flow/cap)^beta."""
    return 1.0 + BPR_ALPHA * (flow / cap) ** BPR_BETA


def routing_graph(net: Network, factor: np.ndarray | None = None) -> nx.DiGraph:
    """Node graph weighted by perceived travel time; closed edges excluded.

    Weight = free-flow time x CLASS_COST x congestion factor + junction penalty.

    Parallel edges keep the fastest one; its id is stored as `eid`.

    Args:
        net: Compiled network.
        factor: (E,) congestion multipliers (BPR), or None for free flow.
    Returns:
        DiGraph over node indices with edge attrs `w` (s) and `eid`.
    """
    G = nx.DiGraph()
    G.add_nodes_from(range(len(net.node_xy)))
    for e in range(net.n_edges):
        if net.edge_closed[e]:
            continue
        u, v = int(net.link_from[e]), int(net.link_to[e])
        f = 1.0 if factor is None or e >= len(factor) else float(factor[e])
        w = (net.link_len[e] / net.link_vmax[e] * CLASS_COST.get(net.edge_highway[e], 1.3) * f
             + NODE_PENALTY_S.get(int(net.node_kind[v]), 0.0))
        if not G.has_edge(u, v) or G[u][v]["w"] > w:
            G.add_edge(u, v, w=w, eid=e)
    return G


Path = tuple[int, ...]           # edge ids from origin to destination
PathMix = dict[Path, float]      # path -> share of the OD demand (sums to 1)
MIN_PATH_SHARE = 0.05            # smaller MSA shares are dropped at the end


def _merge(mix: PathMix, path: Path | None, share: float) -> None:
    """Add `share` of the demand to `path` inside `mix` (in place)."""
    if path is not None:
        mix[path] = mix.get(path, 0.0) + share


class RouteCache:
    """Entry->exit routes as path mixes, recomputed selectively.

    With demand given, routes come from a capacity-aware MSA assignment:
    every iteration finds the shortest path on BPR-congested times and
    shifts 1/k of each OD pair's demand onto it. The result is a *mix* of
    paths per OD pair, so parallel roads share the load instead of one
    all-or-nothing shortest path taking everything.

    Attributes:
        paths: (origin node, dest node) -> PathMix; empty if unreachable.
        factor: (E,) congestion multipliers of the final assignment, or None.
    """

    def __init__(self, net: Network, od_rate: dict[tuple[int, int], float] | None = None,
                 factor: np.ndarray | None = None):
        """Precompute every entry x exit route for `net`.

        Args:
            net: Network to route on.
            od_rate: (origin, dest) -> demand (veh/s). When given, routes are
                assigned capacity-aware with MSA; otherwise one shortest path each.
            factor: Fixed congestion multipliers to route with (e.g. a baseline's).
        """
        self.factor = factor
        origins = [int(o) for o in net.entries]
        self.paths: dict[tuple[int, int], PathMix] = {
            k: ({p: 1.0} if p else {}) for k, p in self._shortest(net, origins).items()}
        if od_rate and factor is None:
            self._assign(net, od_rate, origins)

    def edge_flow(self, net: Network, od_rate: dict[tuple[int, int], float]) -> np.ndarray:
        """(E,) expected flow (veh/s) when each OD pair splits over its path mix."""
        flow = np.zeros(net.n_edges)
        for key, rate in od_rate.items():
            for path, share in self.paths.get(key, {}).items():
                flow[list(path)] += rate * share
        return flow

    def _assign(self, net: Network, od_rate: dict[tuple[int, int], float],
                origins: list[int]) -> None:
        """MSA: re-route on BPR times and average the path shares.

        Args:
            net: Network to route on.
            od_rate: (origin, dest) -> demand (veh/s).
            origins: Origin nodes.
        """
        cap = edge_capacity(net)
        for k in range(2, ASSIGN_ITERATIONS + 1):
            self.factor = bpr_factor(self.edge_flow(net, od_rate), cap)
            best = self._shortest(net, origins)
            for key, mix in self.paths.items():
                for path in mix:
                    mix[path] *= 1.0 - 1.0 / k
                _merge(mix, best[key], 1.0 / k)
        for key, mix in self.paths.items():
            kept = {p: w for p, w in mix.items() if w >= MIN_PATH_SHARE}
            total = sum(kept.values())
            self.paths[key] = {p: w / total for p, w in kept.items()}
        self.factor = bpr_factor(self.edge_flow(net, od_rate), cap)

    def _shortest(self, net: Network, origins: list[int],
                  only: set[tuple[int, int]] | None = None) -> dict[tuple[int, int], Path | None]:
        """One Dijkstra per origin on the current weights.

        Args:
            net: Network to route on.
            origins: Origin nodes to compute.
            only: If given, only these (o, d) pairs are returned.
        Returns:
            (o, d) -> shortest edge path, or None if unreachable.
        """
        G = routing_graph(net, self.factor)
        out = {}
        for o in origins:
            if o not in G:  # every road out of the origin is closed
                continue
            _, node_paths = nx.single_source_dijkstra(G, o, weight="w")
            for d in (int(x) for x in net.exits):
                if d == o or (only is not None and (o, d) not in only):
                    continue
                nodes = node_paths.get(d)
                out[(o, d)] = (tuple(G[a][b]["eid"] for a, b in zip(nodes[:-1], nodes[1:],
                                                                    strict=True))
                               if nodes else None)
        return out

    def with_closures(self, net: Network, closed_edges: set[int]) -> "RouteCache":
        """Copy of this cache where only paths using `closed_edges` are replaced.

        In each affected OD pair the share of the blocked paths moves to the
        new shortest path (same congestion factors); other paths are kept.

        Args:
            net: The *scenario* network (closed edges flagged).
            closed_edges: Edge ids newly closed in the scenario.
        Returns:
            New RouteCache; `self` is unchanged. `recomputed` holds the pair count.
        """
        new = copy.copy(self)
        new.paths = dict(self.paths)
        affected = {k for k, mix in self.paths.items()
                    if any(closed_edges.intersection(p) for p in mix)}
        best = new._shortest(net, sorted({o for o, _ in affected}), only=affected)
        for key in affected:
            mix = {p: w for p, w in self.paths[key].items() if not closed_edges.intersection(p)}
            # best.get: the pair may be cut off entirely (e.g. its exit road closed)
            _merge(mix, best.get(key), 1.0 - sum(mix.values()))
            total = sum(mix.values())
            new.paths[key] = {p: w / total for p, w in mix.items()} if total > 0 else {}
        new.recomputed = len(affected)
        return new


@dataclass
class RouteTable:
    """Flat link sequences for the engine.

    Attributes:
        links: Concatenated link ids of all routes.
        off: (R,) start offset of each route in `links`.
        length: (R,) number of links per route.
        free_time: (R,) free-flow travel time (s).
        index: (origin, dest) -> (route ids, cumulative shares); missing if unreachable.
    """

    links: np.ndarray
    off: np.ndarray
    length: np.ndarray
    free_time: np.ndarray
    index: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]]

    def pick(self, origin: int, dest: int, vid: int) -> int | None:
        """Route for vehicle `vid`, drawn from its OD path mix.

        The draw is a fixed hash of the vehicle id, so it is reproducible and
        identical between baseline and scenario runs.

        Returns:
            Route id, or None if the pair is unreachable.
        """
        entry = self.index.get((origin, dest))
        if entry is None:
            return None
        ids, cum = entry
        u = ((vid * 2654435761 + 12345) % 2**32) / 2**32
        return int(ids[min(int(np.searchsorted(cum, u * cum[-1], side="right")), len(ids) - 1)])


def _edges_to_links(net: Network, edges: list[int]) -> list[int] | None:
    """Interleave junction movements between consecutive edges (None if a turn is missing)."""
    links = [edges[0]]
    for a, b in zip(edges[:-1], edges[1:], strict=True):
        m = net.mv_index.get((a, b))
        if m is None:
            return None
        links += [net.n_edges + m, b]
    return links


def build_route_table(net: Network, cache: RouteCache) -> RouteTable:
    """Convert a RouteCache into a RouteTable for `net`.

    Args:
        net: Network whose movement ids are used.
        cache: Path mixes (must match `net`'s edge ids).
    Returns:
        RouteTable with only reachable pairs.
    """
    flat, off, length, free, index = [], [], [], [], {}
    for key in sorted(cache.paths):
        ids, shares = [], []
        for path, share in sorted(cache.paths[key].items()):
            links = _edges_to_links(net, list(path))
            if not links:
                continue
            ids.append(len(off))
            shares.append(share)
            off.append(len(flat))
            length.append(len(links))
            free.append(float(np.sum(net.link_len[links] / net.link_vmax[links])))
            flat.extend(links)
        if ids:
            index[key] = (np.asarray(ids, np.int32), np.cumsum(shares))
    return RouteTable(np.asarray(flat, np.int32), np.asarray(off, np.int32),
                      np.asarray(length, np.int32), np.asarray(free), index)
