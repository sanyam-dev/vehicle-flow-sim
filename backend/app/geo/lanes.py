"""Road attribute parsing: lanes per direction, speed limits, road-class rank.

OSM tags are messy (strings, lists after simplification, ";"-separated
values). Every parser here is defensive and falls back to defaults by
`highway` class. Defaults are tuned for Indian urban roads.
"""

import ast
from typing import Any

# Lanes per direction when the `lanes` tag is missing.
DEFAULT_LANES: dict[str, int] = {
    "motorway": 3, "trunk": 3, "primary": 2, "secondary": 2,
    "tertiary": 1, "unclassified": 1, "residential": 1, "service": 1,
    "living_street": 1,
}

# Urban speed limits (km/h) when `maxspeed` is missing.
DEFAULT_SPEED_KMH: dict[str, float] = {
    "motorway": 80, "trunk": 60, "primary": 50, "secondary": 40,
    "tertiary": 35, "unclassified": 30, "residential": 25, "service": 15,
    "living_street": 15,
}

# Higher rank = higher priority at unsignalised junctions.
CLASS_RANK: dict[str, int] = {
    "motorway": 7, "trunk": 6, "primary": 5, "secondary": 4,
    "tertiary": 3, "unclassified": 2, "residential": 1, "service": 0,
    "living_street": 0,
}

MAX_LANES = 6


def as_list(value: Any) -> list:
    """Normalise an OSM attribute to a list.

    GraphML round-trips turn lists into strings like "['2', '3']"; those are
    parsed back. Scalars become one-element lists, None an empty list.

    Args:
        value: Raw OSM attribute (scalar, list, stringified list, or None).
    """
    if value is None:
        return []
    if isinstance(value, str) and value.startswith("["):
        try:
            value = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return [value]
    return list(value) if isinstance(value, (list, tuple)) else [value]


def first(value: Any) -> Any:
    """Return the first element of a (possibly list-valued) attribute, or None.

    Args:
        value: Raw OSM attribute (scalar, list, stringified list, or None).
    """
    vals = as_list(value)
    return vals[0] if vals else None


def base_class(highway: Any) -> str:
    """Normalise a `highway` tag to its base class ("primary_link" -> "primary").

    Args:
        highway: Raw `highway` attribute (str or list).
    Returns:
        Base class string, "unclassified" if unknown.
    """
    hw = str(first(highway) or "unclassified")
    hw = hw.removesuffix("_link")
    return hw if hw in DEFAULT_LANES else "unclassified"


def is_link(highway: Any) -> bool:
    """True when the road is a ramp / slip road (`*_link`)."""
    return str(first(highway) or "").endswith("_link")


def _to_int(value: Any) -> int | None:
    """Parse an OSM numeric string like "2", "2;3" or ["2","3"] to its max int."""
    vals = [part for v in as_list(value) for part in str(v).split(";")]
    nums = []
    for v in vals:
        try:
            nums.append(int(float(str(v).strip())))
        except ValueError:
            continue
    return max(nums) if nums else None


def lanes_per_direction(attrs: dict, oneway: bool, reversed_: bool) -> int:
    """Number of lanes for one directed edge.

    Order: lanes:forward/backward -> split total `lanes` -> class default.

    Args:
        attrs: Edge attribute dict from osmnx.
        oneway: Whether the OSM way is one-way.
        reversed_: Whether this directed edge runs against the way direction.
    Returns:
        Lane count clamped to [1, MAX_LANES].
    """
    directional = attrs.get("lanes:backward" if reversed_ else "lanes:forward")
    n = _to_int(directional)
    if n is None:
        total = _to_int(attrs.get("lanes"))
        if total is not None:
            n = total if oneway else max(1, total // 2)
    if n is None:
        n = DEFAULT_LANES[base_class(attrs.get("highway"))]
        if oneway and n == 1 and base_class(attrs.get("highway")) in ("primary", "secondary"):
            n = 2
    return int(min(max(n, 1), MAX_LANES))


def edge_lane_count(attrs: dict) -> int:
    """Lanes of one directed graph edge: the scenario's `n_lanes`, else from OSM tags.

    Args:
        attrs: Edge attribute dict (osmnx tags plus optional `n_lanes`).
    """
    if attrs.get("n_lanes"):
        return int(attrs["n_lanes"])
    oneway = str(first(attrs.get("oneway"))).lower() in ("true", "yes", "1")
    rev = str(first(attrs.get("reversed"))).lower() in ("true", "1")
    return lanes_per_direction(attrs, oneway, rev)


def speed_limit_ms(attrs: dict) -> float:
    """Speed limit in m/s from `maxspeed`, falling back to the class default.

    Args:
        attrs: Edge attribute dict.
    Returns:
        Speed limit in metres per second.
    """
    raw = first(attrs.get("maxspeed"))
    kmh = None
    if raw is not None:
        txt = str(raw).lower().replace("km/h", "").strip()
        mph = "mph" in txt
        try:
            kmh = float(txt.replace("mph", "").split(";")[0]) * (1.609 if mph else 1.0)
        except ValueError:
            kmh = None
    if kmh is None or kmh <= 0:
        kmh = DEFAULT_SPEED_KMH[base_class(attrs.get("highway"))]
    return kmh / 3.6


def class_rank(highway: Any) -> int:
    """Priority rank of a road class (higher wins at priority junctions)."""
    return CLASS_RANK[base_class(highway)]
