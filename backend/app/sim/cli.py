"""Command-line runner for the engine (no API, no LLM).

Examples:
    uv run python -m app.sim.cli --place "Silk Board Junction, Bengaluru" \
        --traffic heavy --time 09:00 --duration 1800
    uv run python -m app.sim.cli --place "Silk Board Junction, Bengaluru" \
        --traffic heavy --time 09:00 --close-lanes "Hosur:0"
"""

import argparse
import json
import time
from pathlib import Path

from app.config import get_settings
from app.geo.geocoder import resolve_area
from app.geo.graph_builder import load_or_build
from app.scenario.modifier import close_edges, close_lanes, edges_named, scenario_copy
from app.sim.engine import SimConfig
from app.sim.runner import run_pair


def _parse() -> argparse.Namespace:
    """Command-line arguments."""
    p = argparse.ArgumentParser(description="Run a baseline (and scenario) simulation.")
    p.add_argument("--place", required=True, help="Place name to geocode")
    p.add_argument("--traffic", default=None,
                   choices=["less", "moderate", "high", "normal", "heavy"])
    p.add_argument("--time", default="09:00", help="Start time HH:MM")
    p.add_argument("--duration", type=float, default=1800.0, help="Seconds incl. warm-up")
    p.add_argument("--warmup", type=float, default=300.0)
    p.add_argument("--seed", type=int, default=get_settings().sim_seed)
    p.add_argument("--close-road", action="append", default=[],
                   help="Close every edge whose name contains this text")
    p.add_argument("--close-lanes", action="append", default=[],
                   help="NAME:K1,K2 closes lanes K (0 = kerb) on roads matching NAME")
    p.add_argument("--frames", type=Path, default=None, help="Write frames to this folder")
    p.add_argument("--json", action="store_true", help="Print the full result as JSON")
    return p.parse_args()


def _scenario(G, args):
    """Apply --close-road / --close-lanes to a copy of G (None if no edits)."""
    if not args.close_road and not args.close_lanes:
        return None
    Gs = scenario_copy(G)
    for name in args.close_road:
        edges = edges_named(Gs, name)
        print(f"close road {name!r}: {len(edges)} edges")
        close_edges(Gs, edges)
    for spec in args.close_lanes:
        name, _, lanes = spec.partition(":")
        edges = edges_named(Gs, name)
        print(f"close lanes {lanes or 'all'} of {name!r}: {len(edges)} edges")
        close_lanes(Gs, edges, [int(k) for k in lanes.split(",")] if lanes else None)
    return Gs


def _print_kpis(result: dict) -> None:
    """Pretty-print the KPI table."""
    if result["comparison"]:
        print(f"{'kpi':28s}{'baseline':>12s}{'scenario':>12s}{'delta %':>10s}")
        for k, row in result["comparison"]["kpi"].items():
            pct = "" if row["delta_pct"] is None else f"{row['delta_pct']:+.1f}"
            print(f"{k:28s}{row['baseline']!s:>12s}{row['scenario']!s:>12s}{pct:>10s}")
    else:
        for k, v in result["baseline"]["kpi"].items():
            print(f"{k:28s}{v!s:>12s}")


def main() -> None:
    """Entry point."""
    args = _parse()
    hh, mm = (int(x) for x in args.time.split(":"))
    area = resolve_area(args.place)
    print(f"area {area.name} [{area.area_id}] driving={area.driving_side}")
    G = load_or_build(area)
    cfg = SimConfig(duration_s=args.duration, warmup_s=args.warmup, seed=args.seed)
    t0 = time.perf_counter()
    result = run_pair(G, _scenario(G, args), args.traffic, hh + mm / 60, cfg, args.frames)
    print(f"info {result['info']}  wall {time.perf_counter() - t0:.1f}s")
    print(json.dumps(result, indent=1) if args.json else "", end="")
    _print_kpis(result)


if __name__ == "__main__":
    main()
