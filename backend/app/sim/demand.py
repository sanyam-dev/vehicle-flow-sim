"""Traffic demand: spawn rates per entry and a seeded spawn schedule.

Traffic levels are defined per main road (motorway / trunk / primary
entry, one direction), in vehicles per hour:

    less      500 -  700 veh/h
    moderate 1000 - 1400 veh/h
    high     2000 - 2600 veh/h   ("about 2000 and more")

The time of day picks the value inside the range (bottom off-peak, top at
the daily peak). Minor entries get CLASS_SHARE of the main-road rate, and
no entry exceeds MAX_VPH_PER_LANE per lane. If the level is not given it is
derived from the time of day (peak -> high, daytime -> moderate,
night -> less). "normal" and "heavy" are accepted as old aliases.

Destinations are origin-aware (see `od_probabilities`): a share of the
traffic entering on a main road is through traffic that leaves on the same
road at the far side; all other trips pick exits by a gravity model
(exit attractiveness x exp(-route cost / GRAVITY_COST_S)). The route cost
is the free-flow perceived travel time (minor streets cost more), so exits
that can only be reached through side streets draw few trips.

The whole schedule (time, origin, destination, type, speed factor) is
drawn once from the seed, so baseline and scenario see identical demand.
"""

from dataclasses import dataclass

import networkx as nx
import numpy as np

from app.sim.network import Network
from app.sim.routing import routing_graph
from app.sim.vehicles import TYPES, fleet_mix

# Main-road entry rate range (veh/h) per traffic level.
LEVEL_VPH = {"less": (500.0, 700.0), "moderate": (1000.0, 1400.0), "high": (2000.0, 2600.0)}
LEVEL_ALIASES = {"normal": "moderate", "heavy": "high"}
# Entry rate of each road class as a share of a main-road entry's rate.
CLASS_SHARE = {
    "motorway": 1.0, "trunk": 1.0, "primary": 1.0, "secondary": 0.6, "tertiary": 0.35,
    "unclassified": 0.1, "residential": 0.03, "service": 0.015, "living_street": 0.01,
}
DEFAULT_CLASS_SHARE = 0.15
# Share of an entry's trips that continue along the same named road.
THROUGH_SHARE = {"motorway": 0.7, "trunk": 0.6, "primary": 0.5, "secondary": 0.4}
THROUGH_MIN_M = 400.0   # a through exit must be at least this far from the entry
GRAVITY_COST_S = 90.0   # perceived route-time decay of exit choice
MIN_TRIP_M = 150.0      # exits closer than this to the entry are not chosen
MAX_VPH_PER_LANE = 1500.0
# (hour, factor) knots of the daily profile; linear in between.
TOD_KNOTS = np.array([
    (0, 0.10), (3, 0.06), (5, 0.12), (7, 0.55), (8, 0.85), (9, 1.0), (10, 0.95),
    (11, 0.80), (13, 0.75), (16, 0.80), (17, 0.92), (18, 1.0), (19, 0.95), (20, 0.75),
    (22, 0.40), (24, 0.10),
])


def tod_factor(hour: float) -> float:
    """Relative traffic intensity (0..1) for an hour of day."""
    return float(np.interp(hour % 24, TOD_KNOTS[:, 0], TOD_KNOTS[:, 1]))


def level_from_time(hour: float) -> str:
    """Traffic level implied by the time of day alone."""
    f = tod_factor(hour)
    return "high" if f >= 0.85 else ("moderate" if f >= 0.4 else "less")


def main_road_vph(level: str | None, hour: float) -> float:
    """Entry rate (veh/h) of one main road for a level and hour.

    Args:
        level: "less" | "moderate" | "high" (or alias), None = from the hour.
        hour: Hour of day (0-24).
    Returns:
        Rate inside the level's range, placed by the time-of-day factor.
    """
    name = LEVEL_ALIASES.get(level, level) if level else level_from_time(hour)
    lo, hi = LEVEL_VPH[name]
    return lo + (hi - lo) * tod_factor(hour)


def _out_edge(net: Network, node: int) -> int:
    """First open edge leaving an entry node."""
    leaving = net.link_from[: net.n_edges] == node
    cand = np.flatnonzero(leaving & ~net.edge_closed)
    return int(cand[0]) if len(cand) else int(np.flatnonzero(leaving)[0])


def entry_rates(net: Network, level: str | None, hour: float) -> np.ndarray:
    """Spawn rate (veh/s) for each node in `net.entries`.

    Rate = main-road rate x CLASS_SHARE of the entry road, capped at
    MAX_VPH_PER_LANE per lane.
    """
    main = main_road_vph(level, hour)
    rates = []
    for node in net.entries:
        e = _out_edge(net, int(node))
        vph = main * CLASS_SHARE.get(net.edge_highway[e], DEFAULT_CLASS_SHARE)
        rates.append(min(vph, MAX_VPH_PER_LANE * int(net.link_lanes[e])) / 3600.0)
    return np.asarray(rates)


@dataclass
class Schedule:
    """Pre-drawn spawn events, sorted by time. Index = vehicle id."""

    t: np.ndarray
    origin: np.ndarray
    dest: np.ndarray
    vtype: np.ndarray
    speed_factor: np.ndarray

    def __len__(self) -> int:
        return len(self.t)


def _in_edge(net: Network, node: int) -> int:
    """First edge arriving at an exit node."""
    return int(np.flatnonzero(net.link_to[: net.n_edges] == node)[0])


def _normalise_rows(w: np.ndarray) -> np.ndarray:
    """Row-normalise; rows summing to zero stay zero."""
    total = w.sum(axis=1, keepdims=True)
    return np.divide(w, total, out=np.zeros_like(w), where=total > 0)


def _route_costs(net: Network) -> np.ndarray:
    """(entries, exits) free-flow perceived route time (s); inf if unreachable."""
    G = routing_graph(net)
    cost = np.full((len(net.entries), len(net.exits)), np.inf)
    for i, o in enumerate(net.entries):
        lengths = nx.single_source_dijkstra_path_length(G, int(o), weight="w")
        cost[i] = [lengths.get(int(d), np.inf) for d in net.exits]
    return cost


def od_probabilities(net: Network) -> np.ndarray:
    """Destination probabilities for every (entry, exit) pair.

    P = s * through + (1 - s) * gravity, where s = THROUGH_SHARE of the
    entry road's class (0 for minor roads or when no through exit exists).

    Args:
        net: Network with `entries` and `exits`.
    Returns:
        (len(entries), len(exits)) array; each row sums to 1.
    """
    in_e = [_in_edge(net, int(d)) for d in net.exits]
    out_e = [_out_edge(net, int(o)) for o in net.entries]
    attract = np.array([CLASS_SHARE.get(net.edge_highway[e], DEFAULT_CLASS_SHARE)
                        * int(net.link_lanes[e]) for e in in_e], float)
    dist = np.linalg.norm(net.node_xy[net.entries][:, None] - net.node_xy[net.exits][None],
                          axis=2)
    allowed = (dist >= MIN_TRIP_M) & (net.entries[:, None] != net.exits[None, :])
    cost = _route_costs(net)
    gravity = _normalise_rows(attract * np.exp(-cost / GRAVITY_COST_S) * allowed)
    names_out = np.array([net.edge_name[e].lower() for e in out_e])
    names_in = np.array([net.edge_name[e].lower() for e in in_e])
    same_road = (names_out[:, None] == names_in[None, :]) & (names_out[:, None] != "")
    through = _normalise_rows(attract * (same_road & (dist >= THROUGH_MIN_M)))
    share = np.array([THROUGH_SHARE.get(net.edge_highway[e], 0.0) for e in out_e])
    share = np.where(through.sum(axis=1) > 0, share, 0.0)[:, None]
    p = share * through + (1 - share) * gravity
    fallback = _normalise_rows((net.entries[:, None] != net.exits[None, :]).astype(float))
    return np.where(p.sum(axis=1, keepdims=True) > 0, p, fallback)


def build_schedule(net: Network, level: str | None, hour: float, duration_s: float,
                   seed: int) -> Schedule:
    """Draw the Poisson spawn schedule for the whole run.

    Args:
        net: Baseline network (defines entries and exits).
        level: "less" | "moderate" | "high" | None (derive from hour).
        hour: Start hour of the simulated period (0-24).
        duration_s: Simulated seconds (including warm-up).
        seed: Fixed random seed.
    Returns:
        Schedule sorted by spawn time.
    """
    rng = np.random.default_rng(seed)
    rates = entry_rates(net, level, hour)
    od = od_probabilities(net)
    mix = fleet_mix(hour)
    t, o, d = [], [], []
    for i, (node, rate) in enumerate(zip(net.entries, rates, strict=True)):
        k = rng.poisson(rate * duration_s)
        t.append(np.sort(rng.uniform(0, duration_s, k)))
        o.append(np.full(k, node, np.int32))
        d.append(rng.choice(net.exits, size=k, p=od[i]).astype(np.int32))
    t, o, d = np.concatenate(t), np.concatenate(o), np.concatenate(d)
    order = np.argsort(t, kind="stable")
    vtype = rng.choice(len(TYPES), size=len(t), p=mix).astype(np.int8)
    sd = np.array([TYPES[k].v0_sd for k in vtype])
    factor = np.clip(rng.normal(1.0, sd), 0.75, 1.3)
    return Schedule(t[order], o[order], d[order], vtype, factor)
