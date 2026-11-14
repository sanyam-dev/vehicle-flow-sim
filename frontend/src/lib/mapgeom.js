// Colours and small geometry helpers for the map layers (no Leaflet state).

export const COLORS = {
  bg: '#0d121a', grid: '#121924', nodata: '#3a4658', free: '#2ec4a5', slow: '#f5a524',
  jam: '#ef4444', bridge: '#b392f0', underpass: '#5aa9ff', closed: '#ef4444', select: '#ffffff',
  casing: '#0a0e14', boundary: '#7d8aa0', junction: '#6b7a90', light: '#c9d3e0',
  roadMajor: '#55637a', roadMid: '#404c60', roadMinor: '#2c3646', roadUnder: '#1c2430',
  island: '#1d4f43', edit: '#ff7ab6',
};

export const LANE_M = 3.2;
const M_PER_DEG = 111320;
const SPEED_STOPS = [[0.15, COLORS.jam], [0.45, COLORS.slow], [0.75, COLORS.free]];

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
export function metresPerPixel(z, lat) {
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
 * Stop bar across the end of a road: a short line at right angles to it.
 * @param {number[][]} line Leaflet [lat, lon] points of the carriageway centre
 * @param {number} half half the carriageway width in metres
 * @returns {number[][]} two [lat, lon] points
 */
export function stopBar(line, half) {
  const [b, a] = [line.at(-1), line.at(-2)];
  const kx = M_PER_DEG * Math.cos((b[0] * Math.PI) / 180);
  const [dx, dy] = [(b[1] - a[1]) * kx, (b[0] - a[0]) * M_PER_DEG];
  const len = Math.hypot(dx, dy) || 1;
  const [nx, ny] = [(-dy / len) * half, (dx / len) * half];
  return [[b[0] + ny / M_PER_DEG, b[1] + nx / kx], [b[0] - ny / M_PER_DEG, b[1] - nx / kx]];
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
export function byZoom(z, stops) {
  if (z <= stops[0][0]) return stops[0][1];
  for (let i = 1; i < stops.length; i++) {
    const [z1, v1] = stops[i];
    const [z0, v0] = stops[i - 1];
    if (z <= z1) return v0 + ((z - z0) / (z1 - z0)) * (v1 - v0);
  }
  return stops.at(-1)[1];
}

/** Distance in metres between two {lat, lon} points (small areas). */
export function metresBetween(a, b) {
  const kx = M_PER_DEG * Math.cos((a.lat * Math.PI) / 180);
  return Math.hypot((a.lon - b.lon) * kx, (a.lat - b.lat) * M_PER_DEG);
}
