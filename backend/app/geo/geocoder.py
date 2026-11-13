"""Place-name -> bounding box, via OSM Nominatim.

Rules (from Nominatim's usage policy and CLAUDE.md):
  * at most ~1 request per second, enforced across processes with a file lock;
  * every response is cached in SQLite, keyed by the normalised query;
  * a generic User-Agent is sent (no personal contact data).

The LLM never produces coordinates; only this module does.
"""

import fcntl
import json
import math
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


class GeocodeError(RuntimeError):
    """Raised when a place cannot be resolved."""


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


def search(query: str, **params) -> list[dict]:
    """Nominatim search with caching and rate limiting.

    Args:
        query: Free-text place query.
        **params: Extra Nominatim parameters (viewbox, bounded, polygon_geojson...).
    Returns:
        List of Nominatim result dicts (may be empty).
    """
    key = json.dumps({"q": " ".join(query.lower().split()), **params}, sort_keys=True)
    con = _cache()
    row = con.execute("SELECT resp FROM geocode WHERE q = ?", (key,)).fetchone()
    if row:
        return json.loads(row[0])
    s = get_settings()
    req = {"q": query, "format": "jsonv2", "addressdetails": 1, "limit": 5, **params}
    with _rate_limit():
        resp = httpx.get(s.nominatim_url, params=req, timeout=20,
                         headers={"User-Agent": s.user_agent})
    resp.raise_for_status()
    data = resp.json()
    con.execute("INSERT OR REPLACE INTO geocode VALUES (?, ?)", (key, json.dumps(data)))
    con.commit()
    return data


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


def resolve_area(place: str, subarea: str | None = None,
                 border_streets: list[str] | None = None) -> Area:
    """Resolve the agent's location spec into a capped simulation Area.

    Args:
        place: City / locality / landmark ("Silk Board Junction, Bengaluru").
        subarea: Optional finer place inside `place` ("HSR Layout").
        border_streets: Optional streets bounding the box.
    Returns:
        Area with bbox clamped to the configured size cap.
    Raises:
        GeocodeError: if nothing matches.
    """
    s = get_settings()
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
                country_code=cc, driving_side="left" if cc in LEFT_HAND_COUNTRIES else "right")
