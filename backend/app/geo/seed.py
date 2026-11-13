"""Seed the data dir with areas that ship with the app.

`backend/seed/` holds road graphs (`graphs/<area_id>.graphml`) and the
matching geocoder answers (`geocode.json`, rows of the geocode cache). On
start-up they are copied into the data dir unless already there, so these
areas work without calling Nominatim or Overpass (useful on a fresh cloud
instance, whose disk starts empty, and when public Overpass servers are down).
"""

import json
import shutil
from pathlib import Path

from app.config import get_settings
from app.geo.geocoder import _cache


def install_seed(seed_dir: Path | None = None) -> int:
    """Copy seeded graphs and geocoder rows into the data dir (never overwrites).

    Args:
        seed_dir: Folder with `graphs/` and `geocode.json`; defaults to settings.seed_dir.
    Returns:
        Number of graphs + geocode rows newly installed.
    """
    seed_dir = Path(seed_dir or get_settings().seed_dir)
    if not seed_dir.is_dir():
        return 0
    added = 0
    for src in sorted((seed_dir / "graphs").glob("*.graphml")):
        dst = get_settings().path("graphs", src.name)
        if not dst.exists():
            shutil.copyfile(src, dst)
            added += 1
    rows_file = seed_dir / "geocode.json"
    if rows_file.exists():
        con = _cache()
        for row in json.loads(rows_file.read_text()):
            cur = con.execute("INSERT OR IGNORE INTO geocode VALUES (?, ?)",
                              (row["key"], json.dumps(row["resp"])))
            added += cur.rowcount
        con.commit()
    return added
