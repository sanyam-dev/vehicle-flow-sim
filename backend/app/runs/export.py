"""Convert engine artefacts into compact, browser-friendly JSON.

* `network_geojson`: road edges (LineStrings) and junctions (Points) in
  WGS84, with the attributes the map needs for styling and editing.
* `chunk_json`: one recorder chunk with positions quantised to
  1e-6 degree (~0.1 m) integers so the JSON stays small.
"""

from pathlib import Path

import numpy as np

from app.geo.grade import GRADE_NAMES
from app.sim.network import BOUNDARY, PRIORITY, SIGNAL, Network

QUANT_DEG = 1e-6
KIND_NAMES = {PRIORITY: "priority", SIGNAL: "signal", BOUNDARY: "boundary"}


def _edge_lonlat(net: Network, e: int) -> list[list[float]]:
    """WGS84 polyline of edge `e`, rounded to 6 decimals."""
    pts = net.geom.polyline(e)
    lon, lat = net.to_lonlat.transform(pts[:, 0], pts[:, 1])
    return [[round(float(a), 6), round(float(b), 6)] for a, b in zip(lon, lat, strict=True)]


def _edge_features(net: Network) -> list[dict]:
    """One LineString feature per directed edge."""
    E = net.n_edges
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
                "vmax_kmh": round(float(net.link_vmax[e]) * 3.6, 1),
                "grade": GRADE_NAMES.get(int(net.link_grade[e]), "road"),
                "two_way": (v, u) in pairs, "closed": bool(net.edge_closed[e]),
                "added": bool(net.edge_added[e]), "uvk": f"{uk}-{vk}-{k}",
                "from": u, "to": v,
            },
        })
    return feats


def _node_features(net: Network) -> list[dict]:
    """Point features for junctions and boundary (entry / exit) nodes."""
    lon, lat = net.to_lonlat.transform(net.node_xy[:, 0], net.node_xy[:, 1])
    feats = []
    for n, kind in enumerate(net.node_kind.tolist()):
        if kind not in KIND_NAMES:
            continue
        feats.append({
            "type": "Feature", "id": 1_000_000 + n,
            "geometry": {"type": "Point",
                         "coordinates": [round(float(lon[n]), 6), round(float(lat[n]), 6)]},
            "properties": {"kind": KIND_NAMES[kind], "node": n,
                           "label": net.node_label[n], "osm": str(net.node_id[n])},
        })
    return feats


def network_geojson(net: Network, origin: tuple[float, float]) -> dict:
    """GeoJSON FeatureCollection of the compiled network.

    Args:
        net: Compiled network.
        origin: (lon, lat) the recorder uses as the frame offset origin.
    Returns:
        FeatureCollection with an extra `meta` member (origin, bbox, counts).
    """
    feats = _edge_features(net) + _node_features(net)
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
