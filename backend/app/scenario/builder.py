"""Turn a RunRequest's scenario edits into the baseline and scenario graphs.

    build_graphs(G, req) -> (baseline graph, scenario graph | None, manifest)

The manifest lists every applied edit with the graph edges / node it
touched. After the run, `edit_usage` reads the scenario's usage counters
through it, so the UI can show that vehicles honoured each edit (how many
used the new flyover, that nothing entered a closed road, ...).
"""

import networkx as nx

from app.scenario import edits
from app.scenario.modifier import close_edges, close_lanes, edges_named, scenario_copy


def _key(edge: tuple) -> str:
    """Edge (u, v, key) as the "u-v-k" string the API uses."""
    return "-".join(str(x) for x in edge)


def _apply_splits(G: nx.MultiDiGraph, req) -> tuple[nx.MultiDiGraph, list[tuple], dict]:
    """Resolve the clicked points of every map edit, splitting roads as needed.

    Returns:
        (graph with the splits (G itself if there are no map edits),
         [(node a, node b)] per map edit, split bookkeeping).
    """
    if not req.map_edits:
        return G, [], {}
    H, splits, pairs = G.copy(), {}, []
    for i, e in enumerate(req.map_edits, 1):
        a, b = (edits.resolve_point(H, p.lat, p.lon, splits, p.grade, p.edge)
                for p in (e.a, e.b))
        if a == b:
            raise edits.EditError(f"Map edit {i}: the two points are the same place.")
        pairs.append((a, b))
    return H, pairs, splits


def _closures(Gs: nx.MultiDiGraph, req, splits: dict) -> list[dict]:
    """Apply road, edge and lane closures; returns their manifest entries."""
    out = []
    for name in req.close_roads:
        found = edges_named(Gs, name)
        close_edges(Gs, found)
        out.append({"kind": "close_road", "label": f"Close road “{name}”",
                    "edges": [_key(e) for e in found]})
    for s in req.close_edges:
        u, v, k = (int(x) for x in s.split("-"))
        found = [e for e in edits.expand((u, v, k), splits) if Gs.has_edge(*e)]
        close_edges(Gs, found)
        out.append({"kind": "close_edge", "label": f"Close edge {s}",
                    "edges": [_key(e) for e in found]})
    for c in req.close_lanes:
        found = edges_named(Gs, c.road)
        close_lanes(Gs, found, c.lanes or None)
        lanes = ", ".join(str(k) for k in c.lanes) or "all"
        out.append({"kind": "close_lanes", "label": f"Close lanes {lanes} of “{c.road}”",
                    "edges": [_key(e) for e in found], "lanes": list(c.lanes)})
    return out


def _map_edits(Gs: nx.MultiDiGraph, req, pairs: list[tuple]) -> list[dict]:
    """Apply flyovers, underpasses and extra lanes; returns their manifest entries."""
    out = []
    for e, (a, b) in zip(req.map_edits, pairs, strict=True):
        if e.kind == "add_lane":
            found = edits.add_lane(Gs, a, b, e.both_directions)
            label = "Extra lane" + (" (both directions)" if e.both_directions else "")
        else:
            found = edits.add_grade_road(Gs, a, b, e.kind, e.lanes)
            label = f"New {e.kind}, {e.lanes} lane{'s' if e.lanes > 1 else ''} each way"
        out.append({"kind": e.kind, "label": label, "edges": [_key(x) for x in found]})
    return out


def _junctions(Gs: nx.MultiDiGraph, req) -> list[dict]:
    """Apply junction control edits; returns their manifest entries."""
    out = []
    for j in req.junction_edits:
        edits.set_junction_control(Gs, int(j.node), j.control, j.stop_s)
        out.append({"kind": f"junction_{j.control}", "node": int(j.node), "stop_s": j.stop_s,
                    "label": "Circle" if j.control == "circle" else "Traffic signal"})
    return out


def build_graphs(G: nx.MultiDiGraph, req) -> tuple[nx.MultiDiGraph, nx.MultiDiGraph | None,
                                                   list[dict]]:
    """Baseline and scenario graphs for a run.

    Args:
        G: Cached road graph of the area (never modified).
        req: Validated RunRequest.
    Returns:
        (baseline graph, scenario graph or None when there are no edits, manifest).
    Raises:
        edits.EditError: if an edit cannot be applied (message for the user).
    """
    if not req.has_scenario:
        return G, None, []
    G_base, pairs, splits = _apply_splits(G, req)
    Gs = scenario_copy(G_base)
    manifest = _closures(Gs, req, splits) + _map_edits(Gs, req, pairs) + _junctions(Gs, req)
    return G_base, Gs, manifest


def _usage_text(kind: str, vehicles: int, extra: dict) -> str:
    """One line telling the user what the vehicles did with an edit."""
    if kind in ("flyover", "underpass"):
        return f"{vehicles} vehicles used it"
    if kind == "add_lane":
        return f"{vehicles} vehicles on the widened road, {extra['new_lane_s']} s in the new lane"
    if kind in ("close_road", "close_edge"):
        if not extra["edges"]:
            return "no matching road in this area: nothing was closed"
        return f"{vehicles} vehicles entered (closed)"
    if kind == "close_lanes":
        return f"{extra['closed_lane_s']} s spent in closed lanes"
    if kind == "junction_signal":
        return f"{vehicles} vehicles passed, {extra['red_entries']} entered on red"
    return f"{vehicles} vehicles passed, mean delay {extra['delay_s']} s"


def edit_usage(manifest: list[dict], net, summary: dict) -> list[dict]:
    """How the scenario's vehicles used each edit.

    Args:
        manifest: Output of `build_graphs`.
        net: Compiled scenario Network.
        summary: The scenario run's metrics summary (with `usage`).
    Returns:
        One dict per edit: {kind, label, vehicles, text, ...}.
    """
    use = summary["usage"]
    edge_of = {_key(k): e for e, k in enumerate(net.edge_uvk)}
    node_of = {int(n): i for i, n in enumerate(net.node_id.tolist())}
    by_node = {j["node"]: j for j in summary["junctions"]}
    out = []
    for m in manifest:
        ids = [edge_of[k] for k in m.get("edges", []) if k in edge_of]
        vehicles = sum(use["edge_entries"][e] for e in ids)
        extra = {"closed_lane_s": use["closed_lane_seconds"], "red_entries": use["red_entries"],
                 "new_lane_s": sum(use["lane_seconds"][e][-1] for e in ids), "delay_s": None,
                 "edges": len(ids)}
        if "node" in m:
            row = by_node.get(node_of.get(m["node"], -1), {})
            vehicles, extra["delay_s"] = row.get("vehicles", 0), row.get("delay_mean_s")
            m = {**m, "junction": row.get("label", "")}
        out.append({**{k: v for k, v in m.items() if k != "edges"}, "edges": len(ids),
                    "vehicles": int(vehicles), "text": _usage_text(m["kind"], vehicles, extra)})
    return out
