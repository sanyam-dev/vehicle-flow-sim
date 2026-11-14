"""Run a baseline and (optionally) a scenario with identical demand, then compare.

Both runs share the spawn schedule and the signal stop duration. The
scenario's routes reuse the baseline route cache and only recompute the
entry->exit pairs whose path uses a newly closed edge; if roads or lanes
were added, or a junction's control changed, the routes are assigned again
on the scenario network since any of them could change.
"""

from dataclasses import dataclass
from pathlib import Path

import networkx as nx
import numpy as np

from app.sim.demand import Schedule, build_schedule
from app.sim.engine import Engine, SimConfig
from app.sim.metrics import compare
from app.sim.network import Network, compile_network
from app.sim.routing import RouteCache, RouteTable, build_route_table
from app.sim.signals import SignalPlans, build_signal_plans, plans_json


@dataclass
class Prepared:
    """Everything an Engine needs for one variant."""

    net: Network
    cache: RouteCache
    routes: RouteTable
    plans: SignalPlans


def od_rates(schedule: Schedule, duration_s: float) -> dict[tuple[int, int], float]:
    """(origin, dest) -> scheduled demand (veh/s)."""
    od, counts = np.unique(np.stack([schedule.origin, schedule.dest]), axis=1,
                           return_counts=True)
    return {(int(o), int(d)): c / duration_s for (o, d), c in zip(od.T, counts, strict=True)}


def prepare_baseline(G: nx.MultiDiGraph, level: str | None, hour: float,
                     cfg: SimConfig) -> tuple[Prepared, Schedule, np.ndarray]:
    """Compile the base graph, route it, draw demand and time the signals.

    Returns:
        (prepared baseline, schedule, expected edge flow).
    """
    net = compile_network(G)
    schedule = build_schedule(net, level, hour, cfg.duration_s, cfg.seed)
    od = od_rates(schedule, cfg.duration_s)
    cache = RouteCache(net, od)
    routes = build_route_table(net, cache)
    flow = cache.edge_flow(net, od)  # expected veh/s per edge, times the signals
    plans = build_signal_plans(net, flow, cfg.signal_stop_s)
    return Prepared(net, cache, routes, plans), schedule, flow


def prepare_scenario(G: nx.MultiDiGraph, base: Prepared, flow: np.ndarray,
                     cfg: SimConfig, od: dict | None = None) -> Prepared:
    """Compile the modified graph and re-route only what changed.

    Args:
        G: Scenario graph (the baseline graph plus edits).
        base: Prepared baseline.
        flow: (E,) expected baseline edge flow (veh/s).
        cfg: Run settings (signal stop duration).
        od: (origin, dest) -> demand (veh/s), for a fresh assignment when
            the scenario adds capacity.
    """
    net = compile_network(G, base.net)
    E0 = base.net.n_edges
    widened = (net.link_lanes[:E0] != base.net.link_lanes[:E0]).any()
    rebuilt = (net.node_kind != base.net.node_kind).any()
    if net.n_edges > E0 or widened or rebuilt:
        # New roads, extra lanes or another junction control: any route could
        # change, so assign again on the scenario network.
        cache = RouteCache(net, od)
        cache.recomputed = len(cache.paths)
    else:
        newly_closed = set(np.flatnonzero(net.edge_closed[:E0] & ~base.net.edge_closed).tolist())
        cache = base.cache.with_closures(net, newly_closed) if newly_closed else base.cache
    plans = build_signal_plans(net, flow, cfg.signal_stop_s)
    return Prepared(net, cache, build_route_table(net, cache), plans)


def run_pair(G_base: nx.MultiDiGraph, G_scen: nx.MultiDiGraph | None, level: str | None,
             hour: float, cfg: SimConfig, out_dir: Path | None = None) -> dict:
    """Run baseline (and scenario if given) in fast mode and compare.

    Args:
        G_base: Base road graph.
        G_scen: Modified graph, or None for a baseline-only run.
        level: Traffic level or None (derived from hour).
        hour: Start hour of day.
        cfg: Run settings (seed, duration, warm-up).
        out_dir: Frames go to <out_dir>/baseline and <out_dir>/scenario.
    Returns:
        {"baseline": summary, "scenario": summary | None, "comparison": dict | None,
         "signals": {"baseline": [...], "scenario": [...] | None}, "info": {...}}.
    """
    base, schedule, flow = prepare_baseline(G_base, level, hour, cfg)
    sub = (lambda name: Path(out_dir) / name) if out_dir else (lambda name: None)
    result = {"baseline": Engine(base.net, base.routes, schedule, base.plans, cfg,
                                 sub("baseline")).run(),
              "scenario": None, "comparison": None,
              "signals": {"baseline": plans_json(base.net, base.plans), "scenario": None},
              "info": {"vehicles_scheduled": len(schedule), "nodes": len(base.net.node_xy),
                       "edges": base.net.n_edges, "signals": len(base.plans.nodes)}}
    if G_scen is not None:
        scen = prepare_scenario(G_scen, base, flow, cfg, od_rates(schedule, cfg.duration_s))
        result["scenario"] = Engine(scen.net, scen.routes, schedule, scen.plans, cfg,
                                    sub("scenario")).run()
        result["comparison"] = compare(result["baseline"], result["scenario"])
        result["signals"]["scenario"] = plans_json(scen.net, scen.plans)
        result["info"]["routes_recomputed"] = getattr(scen.cache, "recomputed", 0)
    return result
