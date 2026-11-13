"""Shipped areas (backend/seed) work without Nominatim or Overpass."""

import httpx

from app.config import get_settings
from app.geo.seed import install_seed

SEEDED_PLACE = "Silk Board Junction, Bengaluru"


def test_seed_installs_once_and_resolves_offline(data_dir, monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("unexpected network call")
    monkeypatch.setattr(httpx, "get", boom)

    assert install_seed() >= 2  # at least one graph + its geocoder row
    assert install_seed() == 0  # never overwrites / duplicates

    from app.geo.geocoder import resolve_area
    from app.geo.graph_builder import load_or_build

    area = resolve_area(SEEDED_PLACE)
    assert get_settings().path("graphs", f"{area.area_id}.graphml").exists()
    G = load_or_build(area)  # loads the cached graph, no Overpass call
    assert G.number_of_edges() > 100
