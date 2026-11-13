"""Overpass API client for the drive road network, with a hard time budget.

Why not osmnx's own downloader: for every attempt osmnx first GETs
`/status` (waits the full request timeout on an unreachable host), then
sleeps a 60 s default pause, then POSTs; and it retries 429/504 answers
forever. With today's overloaded public servers that turned one download
into 8+ minutes or an endless wait.

Here every server gets a 5 s TCP reachability check, one POST with separate
connect/read timeouts, and any failure moves on to the next server. The
whole download is bounded by `overpass_budget_s`, after which
`OverpassUnavailable` is raised with a readable message. Progress is
reported through an optional `on_stage(text)` callback.
"""

import logging
import socket
import time
from collections.abc import Callable
from urllib.parse import urlsplit

import requests

from app.config import get_settings

log = logging.getLogger(__name__)

# Copied from osmnx 2.1.1 `_overpass._get_network_filter("drive")`, so the
# graph matches what `ox.graph_from_bbox(network_type="drive")` downloaded.
DRIVE_FILTER = (
    '["highway"]["area"!~"yes"]["access"!~"private"]'
    '["highway"!~"abandoned|bridleway|bus_guideway|construction|corridor|cycleway|'
    "elevator|escalator|footway|no|path|pedestrian|planned|platform|proposed|raceway|"
    'razed|rest_area|service|services|steps|track"]'
    '["motor_vehicle"!~"no"]["motorcar"!~"no"]'
    '["service"!~"alley|driveway|emergency_access|parking|parking_aisle|private"]'
)
PASSES = 2  # rounds over the server list
PASS_BACKOFF_S = 5.0  # pause between rounds
MIN_TRY_S = 15.0  # do not start a request with less budget left than this

StageFn = Callable[[str], None]


class OverpassUnavailable(RuntimeError):
    """No Overpass server returned the road network within the time budget."""


def build_query(bbox: tuple[float, float, float, float], server_timeout_s: int) -> str:
    """Overpass QL for every drivable way touching the box, with all its nodes.

    Args:
        bbox: (west, south, east, north) in degrees.
        server_timeout_s: `[timeout:]` hint so the server gives up when we do.
    Returns:
        Query text (XML output, readable by `ox.graph_from_xml`).
    """
    west, south, east, north = bbox
    box = f"{south:.7f},{west:.7f},{north:.7f},{east:.7f}"
    return f"[out:xml][timeout:{server_timeout_s}];(way{DRIVE_FILTER}({box});>;);out;"


def host_of(url: str) -> str:
    """Host name of a server URL (for logs and status text)."""
    return urlsplit(url).hostname or url


def reachable(url: str, connect_s: float) -> bool:
    """True if a TCP connection to the server opens within `connect_s` seconds.

    Args:
        url: Overpass base URL, e.g. "https://overpass-api.de/api".
        connect_s: Connect timeout (s).
    """
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        with socket.create_connection((parts.hostname, port), timeout=connect_s):
            return True
    except OSError:
        return False


def _post(url: str, query: str, connect_s: float, read_s: float) -> tuple[bytes | None, str]:
    """Send one query to one server.

    Args:
        url: Overpass base URL.
        query: Overpass QL text.
        connect_s: Connect timeout (s).
        read_s: Max wait for the answer (s).
    Returns:
        (osm_xml, "") on success, else (None, short failure reason).
    """
    headers = {"User-Agent": get_settings().user_agent}
    try:
        r = requests.post(f"{url}/interpreter", data={"data": query},
                          timeout=(connect_s, read_s), headers=headers)
    except requests.Timeout:
        return None, "timed out"
    except requests.RequestException as exc:
        return None, type(exc).__name__
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    if b"<osm" not in r.content[:1000]:
        return None, "unexpected response"
    if b"<remark>" in r.content:  # server-side error, e.g. its own query timeout
        return None, "server error (query incomplete)"
    return r.content, ""


def _unavailable(failures: list[str]) -> OverpassUnavailable:
    """Readable error listing what each server answered."""
    detail = "; ".join(failures) or "no servers configured"
    return OverpassUnavailable(
        "Couldn't download the road network: every OpenStreetMap (Overpass) server "
        f"is overloaded or unreachable right now ({detail}). Try again in a few minutes.")


def fetch_xml(bbox: tuple[float, float, float, float], on_stage: StageFn | None = None,
              *, clock: Callable[[], float] = time.monotonic,
              sleep: Callable[[float], None] = time.sleep) -> bytes:
    """Download the drive network of `bbox` as OSM XML within the time budget.

    Args:
        bbox: (west, south, east, north) in degrees.
        on_stage: Optional progress callback, given a one-line status text.
        clock, sleep: Injected for tests.
    Returns:
        OSM XML bytes.
    Raises:
        OverpassUnavailable: if no server answered within `overpass_budget_s`.
    """
    s = get_settings()
    mirrors = s.overpass_url_list
    deadline = clock() + s.overpass_budget_s
    failures: list[str] = []
    dead: set[str] = set()  # unreachable once -> skipped in later rounds
    tries, total = 0, PASSES * len(mirrors)
    for round_no in range(PASSES):
        for url in mirrors:
            tries += 1
            if url in dead:
                continue
            if deadline - clock() < MIN_TRY_S:
                raise _unavailable(failures + ["time budget used up"])
            if on_stage:
                on_stage(f"downloading road network from {host_of(url)} (try {tries}/{total})")
            body = _try_server(url, bbox, deadline - clock(), failures, dead)
            if body is not None:
                return body
        if round_no < PASSES - 1:
            sleep(min(PASS_BACKOFF_S, max(deadline - clock() - MIN_TRY_S, 0.0)))
    raise _unavailable(failures)


def _try_server(url: str, bbox, left_s: float, failures: list[str],
                dead: set[str]) -> bytes | None:
    """Check one server is reachable, then query it; record any failure.

    Args:
        url: Overpass base URL.
        bbox: (west, south, east, north) in degrees.
        left_s: Remaining time budget (s).
        failures: Appended with "host: reason" on failure.
        dead: Appended with `url` if the server is unreachable.
    Returns:
        OSM XML bytes, or None on failure.
    """
    s = get_settings()
    host = host_of(url)
    connect_s = min(s.overpass_connect_s, left_s)
    if not reachable(url, connect_s):
        dead.add(url)
        failures.append(f"{host}: unreachable")
        log.warning("Overpass %s unreachable; skipping", host)
        return None
    read_s = max(min(s.overpass_read_s, left_s - connect_s), 1.0)
    body, reason = _post(url, build_query(bbox, int(read_s)), connect_s, read_s)
    if body is None:
        failures.append(f"{host}: {reason}")
        log.warning("Overpass %s failed (%s)", host, reason)
    return body
