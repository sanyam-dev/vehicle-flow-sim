"""Grade separation: classify edges as at-grade road, bridge/flyover or underpass.

In OSM a bridge does not share a node with the road below it, so the
graph topology already keeps grade-separated roads from conflicting.
This module only labels edges (for rendering and metrics) and ramps
(for merge / diverge behaviour).
"""

from typing import Any

from app.geo.lanes import as_list, first, is_link

ROAD, BRIDGE, UNDERPASS = 0, 1, 2
GRADE_NAMES = {ROAD: "road", BRIDGE: "bridge", UNDERPASS: "underpass"}
GRADE_CODES = {v: k for k, v in GRADE_NAMES.items()}

_TRUTHY = {"yes", "viaduct", "true", "1", "covered", "building_passage", "cantilever"}


def _any_truthy(value: Any) -> bool:
    """True if a (possibly list-valued) OSM tag holds a truthy value."""
    return any(str(v).lower() in _TRUTHY for v in as_list(value))


def _layer(value: Any) -> int:
    """Parse the `layer` tag (list or string) to an int, defaulting to 0."""
    try:
        return int(float(str(first(value))))
    except (TypeError, ValueError):
        return 0


def edge_grade(attrs: dict) -> int:
    """Classify a directed edge.

    Args:
        attrs: Edge attributes (bridge, tunnel, layer, optional `grade`).
    Returns:
        ROAD, BRIDGE or UNDERPASS.
    """
    if "grade" in attrs and attrs["grade"] in GRADE_CODES:
        return GRADE_CODES[attrs["grade"]]
    if _any_truthy(attrs.get("tunnel")):
        return UNDERPASS
    if _any_truthy(attrs.get("bridge")):
        return BRIDGE
    layer = _layer(attrs.get("layer"))
    if layer > 0:
        return BRIDGE
    if layer < 0:
        return UNDERPASS
    return ROAD


def ramp_kind(in_attrs: list[dict], out_attrs: list[dict]) -> str | None:
    """Detect whether a node is a merge or diverge ramp node.

    A merge is a node where a `*_link` enters a non-link road; a diverge is
    where a `*_link` leaves a non-link road.

    Args:
        in_attrs: Attributes of edges entering the node.
        out_attrs: Attributes of edges leaving the node.
    Returns:
        "merge", "diverge" or None.
    """
    in_links = [is_link(a.get("highway")) for a in in_attrs]
    out_links = [is_link(a.get("highway")) for a in out_attrs]
    if any(in_links) and not all(in_links) and not any(out_links):
        return "merge"
    if any(out_links) and not all(out_links) and not any(in_links):
        return "diverge"
    return None
