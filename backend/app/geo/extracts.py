"""Offline road network from roads-only Geofabrik extracts (Overpass fallback).

`deploy/gcp/build-extracts.sh` downloads Geofabrik regions (India and its
six zones), keeps only ways tagged `highway` (plus their nodes) with
`osmium tags-filter`, and uploads per region:

    <id>.osm.pbf   roads-only extract
    <id>.poly      Geofabrik boundary polygon (osmosis .poly format)
    <id>.date      OSM data timestamp (optional)

On Cloud Run that bucket is mounted read-only at `osm_extracts_dir`. When
Overpass fails, `cut()` runs `osmium extract` on the smallest region that
contains the area and returns OSM XML of the same shape as an Overpass
answer (every way touching the box, with all its nodes).
"""

import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from app.config import get_settings

CUT_TIMEOUT_S = 180  # osmium scans the whole regional file, streamed from the bucket


@dataclass(frozen=True)
class Region:
    """One prepared extract.

    Attributes:
        id: File stem, e.g. "india-southern-zone".
        pbf: Roads-only extract file.
        polygon: Region boundary (lon/lat degrees).
        osm_date: OSM data timestamp, or "unknown".
    """

    id: str
    pbf: Path
    polygon: BaseGeometry
    osm_date: str


def parse_poly(text: str) -> BaseGeometry:
    """Parse an osmosis `.poly` boundary (as published by Geofabrik).

    Format: a name line, then rings (a name line, "lon lat" lines, "END"),
    then a final "END". Rings whose name starts with "!" are holes.

    Args:
        text: File content.
    Returns:
        (Multi)Polygon: union of the outer rings minus the holes.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    outers, holes = [], []
    ring, is_hole = None, False
    for ln in lines[1:]:
        if ring is None:
            if ln == "END":  # end of file
                break
            ring, is_hole = [], ln.startswith("!")
        elif ln == "END":
            if len(ring) >= 3:
                (holes if is_hole else outers).append(Polygon(ring))
            ring = None
        else:
            lon, lat = ln.split()[:2]
            ring.append((float(lon), float(lat)))
    shape = unary_union(outers)
    return shape.difference(unary_union(holes)) if holes else shape


@lru_cache(maxsize=4)
def regions(folder: str) -> tuple[Region, ...]:
    """Prepared extracts in a folder (cached; restart to pick up new files).

    Args:
        folder: Directory holding `<id>.osm.pbf` + `<id>.poly` pairs.
    Returns:
        Every region with both files present.
    """
    found = []
    for pbf in sorted(Path(folder).glob("*.osm.pbf")):
        rid = pbf.name.removesuffix(".osm.pbf")
        poly, date = pbf.with_name(f"{rid}.poly"), pbf.with_name(f"{rid}.date")
        if not poly.exists():
            continue
        osm_date = date.read_text().strip() if date.exists() else ""
        found.append(Region(rid, pbf, parse_poly(poly.read_text()), osm_date or "unknown"))
    return tuple(found)


def covering(bbox: tuple[float, float, float, float]) -> Region | None:
    """Smallest prepared region containing the whole box, if the fallback is usable.

    Args:
        bbox: (west, south, east, north) in degrees.
    Returns:
        The region (a zone when one contains the box, else a larger one such as
        all of India), or None if no region does, no extracts folder is
        configured, or the `osmium` tool is missing.
    """
    folder = get_settings().osm_extracts_dir
    if folder is None or not Path(folder).is_dir() or shutil.which("osmium") is None:
        return None
    area = box(*bbox)
    fits = [r for r in regions(str(folder)) if r.polygon.contains(area)]
    return min(fits, key=lambda r: r.polygon.area, default=None)


def cut(region: Region, bbox: tuple[float, float, float, float]) -> bytes:
    """Cut the box out of a region's extract with `osmium extract`.

    Args:
        region: Region whose polygon contains the box.
        bbox: (west, south, east, north) in degrees.
    Returns:
        OSM XML of every way touching the box, with all its nodes.
    Raises:
        RuntimeError: if osmium fails or times out.
    """
    west, south, east, north = bbox
    cmd = ["osmium", "extract", "-b", f"{west},{south},{east},{north}",
           "-s", "complete_ways", "-f", "osm", "-o", "-", str(region.pbf)]
    try:
        out = subprocess.run(cmd, capture_output=True, check=True, timeout=CUT_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"osmium extract timed out on {region.id}") from exc
    except subprocess.CalledProcessError as exc:
        msg = exc.stderr.decode(errors="replace").strip()[-300:]
        raise RuntimeError(f"osmium extract failed on {region.id}: {msg}") from exc
    return out.stdout
