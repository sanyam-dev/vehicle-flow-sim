"""Graph Modifier: apply scenario edits to a copy of the base graph.

Edits are expressed as edge attributes that `sim.network.compile_network`
understands, so edge ids stay stable between baseline and scenario:

* `closed=True`            the directed edge is unusable
* `lane_closures=[...]`    JSON list of {"lanes": [k..] | null, "f0", "f1"}
                           (lane 0 = kerb lane; f0/f1 = fraction of length)
* `added=True`             a new road (appended after the original edges)

Only the low-level operations live here; the Pydantic edit models and the
name resolver build on top of them.
"""

import json

import networkx as nx

from app.geo.lanes import first

EdgeKey = tuple[int, int, int]


def edges_named(G: nx.MultiDiGraph, name: str) -> list[EdgeKey]:
    """Directed edges whose OSM name contains `name` (case-insensitive).

    Args:
        G: Road graph.
        name: Substring of the road name, e.g. "Hosur".
    Returns:
        List of (u, v, key).
    """
    needle = name.lower()
    return [(u, v, k) for u, v, k, d in G.edges(keys=True, data=True)
            if needle in str(d.get("name", "")).lower()]


def close_edges(G: nx.MultiDiGraph, edges: list[EdgeKey]) -> None:
    """Close whole directed edges (in place)."""
    for u, v, k in edges:
        G.edges[u, v, k]["closed"] = True


def close_lanes(G: nx.MultiDiGraph, edges: list[EdgeKey], lanes: list[int] | None,
                f0: float = 0.0, f1: float = 1.0) -> None:
    """Close some lanes (or all with lanes=None) over part of each edge (in place).

    Args:
        G: Graph to modify.
        edges: Directed edges.
        lanes: Lane indices (0 = kerb side) or None for every lane.
        f0, f1: Closed span as fractions of the edge length, 0 <= f0 < f1 <= 1.
    """
    for u, v, k in edges:
        d = G.edges[u, v, k]
        spec = d.get("lane_closures") or "[]"
        spec = json.loads(spec) if isinstance(spec, str) else list(spec)
        spec.append({"lanes": lanes, "f0": f0, "f1": f1})
        d["lane_closures"] = json.dumps(spec)


def scenario_copy(G: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Deep copy of the base graph to apply edits on."""
    return G.copy()


def road_names(G: nx.MultiDiGraph) -> list[str]:
    """Distinct road names present in the graph (for prompts and fuzzy matching)."""
    names = {str(first(d.get("name"))) for _, _, d in G.edges(data=True) if d.get("name")}
    return sorted(names)
