"""Place-name -> bounding box, via OSM Nominatim.

Rules (from Nominatim's usage policy and CLAUDE.md):
  * at most ~1 request per second, enforced across processes with a file lock;
  * every response is cached in SQLite, keyed by the normalised query;
  * a generic User-Agent is sent (no personal contact data).

The LLM never produces coordinates; only this module does. A place may also
be "lat, lon" typed by the user (picked out of their message with
`parse_coordinates`, never from LLM output): the area is then centred on that
point and Nominatim's reverse lookup supplies a name and the country.
"""

import fcntl
import json
import math
import re
import sqlite3
import time
from contextlib import contextmanager

import httpx
from pydantic import BaseModel

from app.config import get_settings

MIN_INTERVAL_S = 1.1
LEFT_HAND_COUNTRIES = {
    "in", "gb", "ie", "jp", "au", "nz", "za", "pk", "bd", "lk", "np", "bt",
    "th", "my", "sg", "id", "ke", "ug", "tz", "hk", "mo", "cy", "mt", "mu",
    "jm", "tt", "bw", "zm", "zw", "mz", "na", "sz", "ls", "mw", "bn", "tl",
}


# "21.170240, 72.831062" (Google Maps "copy coordinates") or "21.17 72.83":
# both numbers need >= 2 decimals so times or counts are not mistaken for them.
_LATLON = re.compile(r"(?<![\d.])(-?\d{1,2}\.\d{2,})\s*(?:,\s*|\s+)(-?\d{1,3}\.\d{2,})(?![\d.])")


class GeocodeError(RuntimeError):
    """Raised when a place cannot be resolved."""


def parse_coordinates(text: str, whole: bool = False) -> tuple[float, float] | None:
    """Find a "lat, lon" pair in text.

    Args:
        text: Free text (a user chat message) or a place string.
        whole: If True, the text must be only the coordinates.
    Returns:
        (lat, lon) in degrees, or None if absent or out of range.
    """
    m = _LATLON.fullmatch(text.strip()) if whole else _LATLON.search(text)
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


def format_coordinates(lat: float, lon: float) -> str:
    """Canonical place string for a point, e.g. "21.170240, 72.831062"."""
    return f"{lat:.6f}, {lon:.6f}"


class Area(BaseModel):
    """A simulation area.

    Attributes:
        name: Human readable label.
        south, west, north, east: Bounding box in WGS84 degrees.
        country_code: ISO-3166 alpha-2 (lowercase) or "" if unknown.
        driving_side: "left" or "right".
    """

    name: str
    south: float
    west: float
    north: float
    east: float
    country_code: str = ""
    driving_side: str = "left"

    @property
    def area_id(self) -> str:
        """Stable id: bbox rounded to 4 decimals (~10 m)."""
        return "_".join(f"{v:.4f}" for v in (self.south, self.west, self.north, self.east))


@contextmanager
def _rate_limit():
    """Block until >= MIN_INTERVAL_S has passed since the last request (all processes)."""
    lock_path = get_settings().path("cache", "nominatim.lock")
    with open(lock_path, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        last = float(fh.read().strip() or 0.0)
        wait = MIN_INTERVAL_S - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        try:
            yield
        finally:
            fh.seek(0)
            fh.truncate()
            fh.write(str(time.time()))
            fh.flush()
            fcntl.flock(fh, fcntl.LOCK_UN)


def _cache() -> sqlite3.Connection:
    """Open (and create if needed) the geocode cache database."""
    con = sqlite3.connect(get_settings().path("cache", "geocode.sqlite"))
    con.execute("CREATE TABLE IF NOT EXISTS geocode (q TEXT PRIMARY KEY, resp TEXT)")
    return con


def _cached_get(url: str, key: str, params: dict):
    """GET a Nominatim endpoint through the SQLite cache and the rate limit.

    Args:
        url: Endpoint URL.
        key: Cache key (normalised request).
        params: Query parameters.
    Returns:
        Decoded JSON answer (cached or fresh).
    """
    con = _cache()
    row = con.execute("SELECT resp FROM geocode WHERE q = ?", (key,)).fetchone()
    if row:
        return json.loads(row[0])
    with _rate_limit():
        resp = httpx.get(url, params=params, timeout=20,
                         headers={"User-Agent": get_settings().user_agent})
    resp.raise_for_status()
    data = resp.json()
    con.execute("INSERT OR REPLACE INTO geocode VALUES (?, ?)", (key, json.dumps(data)))
    con.commit()
    return data


def search(query: str, **params) -> list[dict]:
    """Nominatim search with caching and rate limiting.

    Args:
        query: Free-text place query.
        **params: Extra Nominatim parameters (viewbox, bounded, polygon_geojson...).
    Returns:
        List of Nominatim result dicts (may be empty).
    """
    key = json.dumps({"q": " ".join(query.lower().split()), **params}, sort_keys=True)
    req = {"q": query, "format": "jsonv2", "addressdetails": 1, "limit": 5, **params}
    return _cached_get(get_settings().nominatim_url, key, req)


def reverse(lat: float, lon: float) -> dict:
    """Nominatim reverse lookup of a point (cached, rate limited).

    Args:
        lat, lon: Point in degrees.
    Returns:
        Nominatim result dict; contains "error" when nothing is there (e.g. sea).
    """
    url = get_settings().nominatim_url.rsplit("/", 1)[0] + "/reverse"
    key = json.dumps({"reverse": format_coordinates(lat, lon)})
    req = {"lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "format": "jsonv2",
           "addressdetails": 1, "zoom": 17}
    return _cached_get(url, key, req)


def _bbox_of(result: dict) -> tuple[float, float, float, float]:
    """(south, west, north, east) from a Nominatim result's `boundingbox`."""
    s, n, w, e = (float(v) for v in result["boundingbox"])
    return s, w, n, e


def clamp_bbox(bbox: tuple[float, float, float, float], min_half_m: float,
               max_half_m: float) -> tuple[float, float, float, float]:
    """Grow tiny boxes and shrink huge ones around the box centre.

    Args:
        bbox: (south, west, north, east) in degrees.
        min_half_m: Minimum half side in metres (e.g. a junction point).
        max_half_m: Maximum half side in metres (area cap).
    Returns:
        Adjusted (south, west, north, east).
    """
    s, w, n, e = bbox
    lat0, lon0 = (s + n) / 2, (w + e) / 2
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat0))
    half_lat = min(max((n - s) / 2 * m_per_deg_lat, min_half_m), max_half_m) / m_per_deg_lat
    half_lon = min(max((e - w) / 2 * m_per_deg_lon, min_half_m), max_half_m) / m_per_deg_lon
    return lat0 - half_lat, lon0 - half_lon, lat0 + half_lat, lon0 + half_lon


def _border_bbox(place: str, streets: list[str]) -> tuple[float, float, float, float]:
    """Envelope of the named border streets, searched inside `place`.

    Args:
        place: Parent place used as the search context.
        streets: Street names forming the box borders.
    Returns:
        (south, west, north, east) envelope of all found streets.
    Raises:
        GeocodeError: if none of the streets is found.
    """
    boxes = []
    for street in streets:
        hits = search(f"{street}, {place}")
        if hits:
            boxes.append(_bbox_of(hits[0]))
    if not boxes:
        raise GeocodeError(f"None of the border streets were found in {place!r}")
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _side(country_code: str) -> str:
    """Driving side of a country ("left"/"right")."""
    return "left" if country_code in LEFT_HAND_COUNTRIES else "right"


def _area_at_point(lat: float, lon: float) -> Area:
    """Default-size Area centred on a user-given point.

    Raises:
        GeocodeError: if the point is not on land (no reverse-geocoding result).
    """
    s = get_settings()
    hit = reverse(lat, lon)
    if not hit or "error" in hit:
        raise GeocodeError(f"Nothing on OpenStreetMap at {format_coordinates(lat, lon)}")
    short = ", ".join(p.strip() for p in str(hit.get("display_name", "")).split(",")[:3])
    name = f"{short} ({lat:.5f}, {lon:.5f})" if short else format_coordinates(lat, lon)
    bbox = clamp_bbox((lat, lon, lat, lon), s.default_half_side_m, s.max_half_side_m)
    cc = (hit.get("address") or {}).get("country_code", "")
    return Area(name=name, south=bbox[0], west=bbox[1], north=bbox[2], east=bbox[3],
                country_code=cc, driving_side=_side(cc))


def resolve_area(place: str, subarea: str | None = None,
                 border_streets: list[str] | None = None) -> Area:
    """Resolve the agent's location spec into a capped simulation Area.

    Args:
        place: City / locality / landmark ("Silk Board Junction, Bengaluru"),
            or user-given coordinates "lat, lon".
        subarea: Optional finer place inside `place` ("HSR Layout").
        border_streets: Optional streets bounding the box.
    Returns:
        Area with bbox clamped to the configured size cap.
    Raises:
        GeocodeError: if nothing matches.
    """
    s = get_settings()
    if not subarea and not border_streets and (ll := parse_coordinates(place, whole=True)):
        return _area_at_point(*ll)
    query = f"{subarea}, {place}" if subarea else place
    hits = search(query)
    if not hits:
        raise GeocodeError(f"Could not find {query!r} on OpenStreetMap")
    top = hits[0]
    bbox = _border_bbox(place, border_streets) if border_streets else _bbox_of(top)
    bbox = clamp_bbox(bbox, s.default_half_side_m if not border_streets else 150.0,
                      s.max_half_side_m)
    cc = (top.get("address") or {}).get("country_code", "")
    return Area(name=query, south=bbox[0], west=bbox[1], north=bbox[2], east=bbox[3],
                country_code=cc, driving_side=_side(cc))
