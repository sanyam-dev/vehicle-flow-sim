// Leaflet layers for the road network, drawn on one shared canvas renderer.
//
// * Roads are coloured by r = edge speed / speed limit for the current minute
//   (from the run's speed heatmap); r = null means no data yet.
// * Two-way roads are shifted sideways (towards the driving side) so each
//   direction is visible; widths follow lane counts at true scale (3.2 m/lane).
// * A casing under each road marks flyovers / underpasses and the selection.
// * Closed roads are drawn red and dashed. Junction dots are coloured by queue.
// Draw order is insertion order: grid, casings, roads, junctions.

import L from 'leaflet';

export const COLORS = {
  bg: '#0d121a', grid: '#161e2a', nodata: '#3a4658', free: '#2ec4a5', slow: '#f5a524',
  jam: '#ef4444', bridge: '#b392f0', underpass: '#5aa9ff', closed: '#ef4444', select: '#ffffff',
  casing: '#1d2633', boundary: '#7d8aa0', junction: '#5d6b80',
};

const LANE_M = 3.2;
const M_PER_DEG = 111320;
const SPEED_STOPS = [[0.15, COLORS.jam], [0.45, COLORS.slow], [0.75, COLORS.free]];
const QUEUE_COLORS = { [-1]: COLORS.junction, 0: COLORS.free, 1: COLORS.slow, 2: COLORS.jam };

/** '#rrggbb' -> [r, g, b]. */
function rgb(hex) {
  return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
}

/**
 * Colour for a speed ratio (linear blend between the jam / slow / free stops).
 * @param {number|null} r speed / limit, 0..1, or null for no data
 */
export function speedColor(r) {
  if (r === null || r === undefined) return COLORS.nodata;
  if (r <= SPEED_STOPS[0][0]) return SPEED_STOPS[0][1];
  for (let i = 1; i < SPEED_STOPS.length; i++) {
    const [r1, c1] = SPEED_STOPS[i];
    if (r <= r1) {
      const [r0, c0] = SPEED_STOPS[i - 1];
      const u = (r - r0) / (r1 - r0);
      const [a, b] = [rgb(c0), rgb(c1)];
      return `rgb(${a.map((v, k) => Math.round(v + (b[k] - v) * u)).join(',')})`;
    }
  }
  return SPEED_STOPS.at(-1)[1];
}

/** Metres per screen pixel at zoom z and latitude lat. */
function metresPerPixel(z, lat) {
  return (156543.03 * Math.cos((lat * Math.PI) / 180)) / 2 ** z;
}

/**
 * Shift a [lon, lat] polyline sideways.
 * @param {number[][]} coords GeoJSON coordinates
 * @param {number} d metres; positive = left of the travel direction
 * @returns {number[][]} Leaflet [lat, lon] points
 */
export function offsetLine(coords, d) {
  const kx = M_PER_DEG * Math.cos((coords[0][1] * Math.PI) / 180);
  const pts = coords.map(([lon, lat]) => [lon * kx, lat * M_PER_DEG]);
  return pts.map((p, i) => {
    const a = pts[Math.max(i - 1, 0)];
    const b = pts[Math.min(i + 1, pts.length - 1)];
    const len = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
    const [nx, ny] = [-(b[1] - a[1]) / len, (b[0] - a[0]) / len]; // left normal
    return [(p[1] + d * ny) / M_PER_DEG, (p[0] + d * nx) / kx];
  });
}

/**
 * Faint reference grid (~100 m) covering the bbox, as Leaflet polylines points.
 * @param {[number, number, number, number]} bbox west, south, east, north
 */
export function gridLines([w, s, e, n]) {
  const dLat = 100 / M_PER_DEG;
  const dLon = dLat / Math.cos(((s + n) / 2) * (Math.PI / 180));
  const lines = [];
  for (let x = Math.floor(w / dLon) * dLon; x <= e; x += dLon) lines.push([[s, x], [n, x]]);
  for (let y = Math.floor(s / dLat) * dLat; y <= n; y += dLat) lines.push([[y, w], [y, e]]);
  return lines;
}

/** Piecewise-linear interpolation of [zoom, value] stops. */
function byZoom(z, stops) {
  if (z <= stops[0][0]) return stops[0][1];
  for (let i = 1; i < stops.length; i++) {
    const [z1, v1] = stops[i];
    const [z0, v0] = stops[i - 1];
    if (z <= z1) return v0 + ((z - z0) / (z1 - z0)) * (v1 - v0);
  }
  return stops.at(-1)[1];
}

/** Road network on a Leaflet map: build once per network, then restyle cheaply. */
export class NetworkLayer {
  /**
   * @param {L.Map} map
   * @param {(props: object|null) => void} onSelect called with the clicked edge's properties
   */
  constructor(map, onSelect) {
    this.map = map;
    this.onSelect = onSelect;
    this.renderer = L.canvas({ padding: 0.3, tolerance: 6 });
    this.group = L.layerGroup().addTo(map);
    this.edges = new Map(); // edge id -> {p, casing, road, r, closed, sel}
    this.nodes = new Map(); // node index -> {p, marker, q}
    map.on('zoomend', () => this.restyle());
  }

  /** Remove everything. */
  clear() {
    this.group.clearLayers();
    this.edges.clear();
    this.nodes.clear();
  }

  /**
   * Draw a network GeoJSON from the backend (edges + junction/boundary points).
   * @param {object} net FeatureCollection with `meta.bbox` and `meta.driving_side`
   */
  setNetwork(net) {
    this.clear();
    const sign = (net.meta?.driving_side ?? 'left') === 'left' ? 1 : -1;
    const opts = { renderer: this.renderer, interactive: false };
    for (const pts of gridLines(net.meta.bbox)) {
      L.polyline(pts, { ...opts, color: COLORS.grid, weight: 1 }).addTo(this.group);
    }
    const edges = net.features.filter((f) => f.properties.kind === 'edge');
    const lines = edges.map((f) => {
      const p = f.properties;
      const d = p.two_way ? sign * (p.lanes * LANE_M / 2 + 0.3) : 0;
      return d ? offsetLine(f.geometry.coordinates, d)
               : f.geometry.coordinates.map(([lon, lat]) => [lat, lon]);
    });
    const casings = lines.map((pts) => L.polyline(pts, opts).addTo(this.group));
    edges.forEach((f, i) => {
      const p = f.properties;
      const road = L.polyline(lines[i], { renderer: this.renderer, bubblingMouseEvents: false })
        .on('click', () => this.onSelect?.(p)).addTo(this.group);
      this.edges.set(p.edge, { p, casing: casings[i], road, r: null, closed: false, sel: false });
    });
    for (const f of net.features) {
      const p = f.properties;
      if (p.kind === 'edge') continue;
      const [lon, lat] = f.geometry.coordinates;
      const marker = L.circleMarker([lat, lon], opts).addTo(this.group);
      this.nodes.set(p.node, { p, marker, q: -1 });
    }
    this.restyle();
  }

  /** Re-apply every style (after zoom or a new network). */
  restyle() {
    const z = this.map.getZoom();
    const mpp = metresPerPixel(z, this.map.getCenter().lat);
    for (const e of this.edges.values()) this.#styleEdge(e, z, mpp);
    for (const n of this.nodes.values()) this.#styleNode(n, z);
  }

  #styleEdge(e, z = this.map.getZoom(), mpp = metresPerPixel(z, this.map.getCenter().lat)) {
    const w = Math.max(z >= 15 ? 1.2 : 0.7, (e.p.lanes * LANE_M) / mpp);
    const grade = e.p.grade === 'bridge' ? COLORS.bridge
      : e.p.grade === 'underpass' ? COLORS.underpass : COLORS.casing;
    e.casing.setStyle({ color: e.sel ? COLORS.select : grade, weight: w + (e.sel ? 6 : 3),
                        opacity: e.sel ? 0.6 : 1, lineCap: 'butt' });
    e.road.setStyle({ color: e.closed ? COLORS.closed : speedColor(e.r), weight: w,
                      dashArray: e.closed ? '4 4' : null, lineCap: 'butt',
                      opacity: e.p.grade === 'underpass' ? 0.7 : 0.95 });
  }

  #styleNode(n, z = this.map.getZoom()) {
    const { kind } = n.p;
    if (kind === 'boundary') {
      n.marker.setStyle({ radius: 2.5, color: COLORS.boundary, weight: 1,
                          fillColor: COLORS.bg, fillOpacity: 1 });
      return;
    }
    const radius = kind === 'signal' ? byZoom(z, [[13, 3], [17, 7]]) : byZoom(z, [[13, 1.5], [17, 3.5]]);
    n.marker.setStyle({ radius, color: COLORS.bg, weight: 1.5, fillOpacity: 1,
                        fillColor: QUEUE_COLORS[n.q] ?? COLORS.junction });
  }

  /** Speed ratio of one edge for the current minute (null = no data). */
  setSpeed(edge, r) {
    const e = this.edges.get(edge);
    if (e && e.r !== r) { e.r = r; this.#styleEdge(e); }
  }

  /** Queue class of a junction: -1 none, 0 short, 1 building, 2 long. */
  setQueue(node, q) {
    const n = this.nodes.get(node);
    if (n && n.q !== q) { n.q = q; this.#styleNode(n); }
  }

  /** Mark one edge closed / open. */
  setClosed(edge, closed) {
    const e = this.edges.get(edge);
    if (e && e.closed !== closed) { e.closed = closed; this.#styleEdge(e); }
  }

  /** Highlight one edge (or remove the highlight). */
  select(edge, on) {
    const e = this.edges.get(edge);
    if (e && e.sel !== on) { e.sel = on; this.#styleEdge(e); }
  }

  /** Fit the map to the network's bbox [w, s, e, n]. */
  fit([w, s, e, n]) {
    this.map.fitBounds([[s, w], [n, e]], { padding: [40, 40], animate: false });
  }
}
