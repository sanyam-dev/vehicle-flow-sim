"""Convert engine artefacts into compact, browser-friendly JSON.

* `network_geojson`: road edges (LineStrings) and junctions (Points) in
  WGS84, with the attributes the map needs for styling and editing.
* `chunk_json`: one recorder chunk with positions quantised to
  1e-6 degree (~0.1 m) integers so the JSON stays small.
"""

from pathlib import Path

import numpy as np

from app.geo.grade import GRADE_NAMES
from app.sim.movements import RING_SHARE
from app.sim.network import BOUNDARY, PRIORITY, ROUNDABOUT, SIGNAL, Network

QUANT_DEG = 1e-6
KIND_NAMES = {PRIORITY: "priority", SIGNAL: "signal", BOUNDARY: "boundary",
              ROUNDABOUT: "roundabout"}


def _edge_lonlat(net: Network, e: int) -> list[list[float]]:
    """WGS84 polyline of edge `e`, rounded to 6 decimals."""
    pts = net.geom.polyline(e)
    lon, lat = net.to_lonlat.transform(pts[:, 0], pts[:, 1])
    return [[round(float(a), 6), round(float(b), 6)] for a, b in zip(lon, lat, strict=True)]


def _edge_features(net: Network, base: Network) -> list[dict]:
    """One LineString feature per directed edge.

    `base_lanes` is the lane count in the baseline (0 for a road the
    scenario added), so the map can draw each variant correctly.
    """
    E, E0 = net.n_edges, base.n_edges
    pairs = set(zip(net.link_from[:E].tolist(), net.link_to[:E].tolist(), strict=True))
    feats = []
    for e in range(E):
        u, v = int(net.link_from[e]), int(net.link_to[e])
        uk, vk, k = net.edge_uvk[e]
        feats.append({
            "type": "Feature", "id": e,
            "geometry": {"type": "LineString", "coordinates": _edge_lonlat(net, e)},
            "properties": {
                "kind": "edge", "edge": e, "name": net.edge_name[e] or "",
                "road": net.edge_road[e] or "", "highway": net.edge_highway[e],
                "lanes": int(net.link_lanes[e]),
                "base_lanes": int(base.link_lanes[e]) if e < E0 else 0,
                "vmax_kmh": round(float(net.link_vmax[e]) * 3.6, 1),
                "grade": GRADE_NAMES.get(int(net.link_grade[e]), "road"),
                "two_way": (v, u) in pairs, "closed": bool(net.edge_closed[e]),
                "added": bool(net.edge_added[e]), "uvk": f"{uk}-{vk}-{k}",
                "from": u, "to": v,
            },
        })
    return feats


def _node_features(net: Network, base: Network) -> list[dict]:
    """Point features for junctions and boundary (entry / exit) nodes.

    `base_kind` is the node's kind in the baseline, `ring_m` the radius of a
    roundabout's ring, `stop_s` a signal's own stop duration (if edited).
    """
    lon, lat = net.to_lonlat.transform(net.node_xy[:, 0], net.node_xy[:, 1])
    base_kind = dict(zip(base.node_id.tolist(), base.node_kind.tolist(), strict=True))
    feats = []
    for n, kind in enumerate(net.node_kind.tolist()):
        if kind not in KIND_NAMES:
            continue
        stop = net.node_stop_s[n]
        feats.append({
            "type": "Feature", "id": 1_000_000 + n,
            "geometry": {"type": "Point",
                         "coordinates": [round(float(lon[n]), 6), round(float(lat[n]), 6)]},
            "properties": {
                "kind": KIND_NAMES[kind], "node": n, "label": net.node_label[n],
                "osm": str(net.node_id[n]),
                "base_kind": KIND_NAMES.get(base_kind.get(int(net.node_id[n])), "node"),
                "ring_m": round(float(net.node_radius[n]) * RING_SHARE, 1),
                "stop_s": float(stop) if np.isfinite(stop) else None,
            },
        })
    return feats


def network_geojson(net: Network, origin: tuple[float, float],
                    base: Network | None = None) -> dict:
    """GeoJSON FeatureCollection of the compiled network.

    Args:
        net: Compiled network (the scenario's when the run has one).
        origin: (lon, lat) the recorder uses as the frame offset origin.
        base: Baseline network; defaults to `net` (baseline-only run).
    Returns:
        FeatureCollection with an extra `meta` member (origin, bbox, counts).
    """
    base = base or net
    feats = _edge_features(net, base) + _node_features(net, base)
    coords = np.array([c for f in feats if f["properties"]["kind"] == "edge"
                       for c in f["geometry"]["coordinates"]])
    bbox = [float(coords[:, 0].min()), float(coords[:, 1].min()),
            float(coords[:, 0].max()), float(coords[:, 1].max())]
    return {"type": "FeatureCollection", "features": feats,
            "meta": {"origin": list(origin), "bbox": bbox, "edges": net.n_edges,
                     "driving_side": net.driving_side}}


def chunk_json(path: Path) -> dict:
    """Load one recorder chunk and quantise it.

    Args:
        path: `chunk_XXXXX.npz` written by FrameRecorder.
    Returns:
        {"t", "off", "vid", "x", "y", "hdg", "vtype", "speed"}; x / y are
        integer offsets from the origin in QUANT_DEG units, speed is dm/s.
    """
    with np.load(path) as z:
        return {
            "t": [round(float(t), 2) for t in z["t"]],
            "off": z["off"].astype(int).tolist(),
            "vid": z["vid"].astype(int).tolist(),
            "x": np.rint(z["dlon"] / QUANT_DEG).astype(int).tolist(),
            "y": np.rint(z["dlat"] / QUANT_DEG).astype(int).tolist(),
            "hdg": np.rint(z["hdg"]).astype(int).tolist(),
            "vtype": z["vtype"].astype(int).tolist(),
            "speed": np.rint(z["speed"] * 10).astype(int).tolist(),
        }
