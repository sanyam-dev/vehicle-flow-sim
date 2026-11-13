// Recorded-frame access and interpolation for smooth replay.
//
// The backend stores one frame per `frame_dt` seconds, grouped in chunks of
// `chunk_frames`. Positions arrive as integer offsets (1e-6 degree) from the
// area origin; speed arrives in dm/s.

import { api } from './api.js';

const QUANT_DEG = 1e-6;
const KEEP_CHUNKS = 10;
export const VEHICLE_TYPES = ['bike', 'auto', 'car', 'truck'];
const STOPPED_MS = 0.5;

/**
 * Decode one chunk into per-frame typed-array views.
 * @param {object} raw {t, off, vid, x, y, hdg, vtype, speed}
 * @param {[number, number]} origin [lon, lat]
 * @returns {object[]} frames: {t, vid, lon, lat, hdg, vtype, speed}
 */
function decode(raw, origin) {
  const n = raw.vid.length;
  const lon = new Float64Array(n);
  const lat = new Float64Array(n);
  const speed = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    lon[i] = origin[0] + raw.x[i] * QUANT_DEG;
    lat[i] = origin[1] + raw.y[i] * QUANT_DEG;
    speed[i] = raw.speed[i] / 10;
  }
  const vid = Int32Array.from(raw.vid);
  const hdg = Float32Array.from(raw.hdg);
  const vtype = Uint8Array.from(raw.vtype);
  return raw.t.map((t, k) => {
    const a = raw.off[k];
    const b = raw.off[k + 1];
    return { t, vid: vid.subarray(a, b), lon: lon.subarray(a, b), lat: lat.subarray(a, b),
             hdg: hdg.subarray(a, b), vtype: vtype.subarray(a, b), speed: speed.subarray(a, b) };
  });
}

/** Lazily loaded, cached frames of one run variant (baseline or scenario). */
export class FrameSource {
  /**
   * @param {string} runId
   * @param {string} variant 'baseline' | 'scenario'
   * @param {object} meta recorder meta.json {origin, frame_dt, chunk_frames, n_chunks, n_frames}
   */
  constructor(runId, variant, meta) {
    this.runId = runId;
    this.variant = variant;
    this.meta = meta;
    this.chunks = new Map(); // index -> {promise, frames|null}
  }

  /** Total replay length in seconds. */
  get duration() {
    return this.meta.n_frames * this.meta.frame_dt;
  }

  /** Start loading chunk i (no-op if cached); evicts chunks far from i. */
  load(i) {
    if (i < 0 || i >= this.meta.n_chunks || this.chunks.has(i)) return;
    const entry = { frames: null };
    entry.promise = api.chunk(this.runId, this.variant, i)
      .then((raw) => { entry.frames = decode(raw, this.meta.origin); })
      .catch(() => this.chunks.delete(i));
    this.chunks.set(i, entry);
    if (this.chunks.size > KEEP_CHUNKS) {
      const far = [...this.chunks.keys()].sort((a, b) => Math.abs(b - i) - Math.abs(a - i))[0];
      this.chunks.delete(far);
    }
  }

  /** Frame f if its chunk is loaded (also prefetches the next chunk), else null. */
  frame(f) {
    const { chunk_frames: cf, n_frames } = this.meta;
    if (f < 0 || f >= n_frames) return null;
    const c = Math.floor(f / cf);
    this.load(c);
    this.load(c + 1);
    return this.chunks.get(c)?.frames?.[f - c * cf] ?? null;
  }

  /** Resolve once the chunk holding time t is loaded. */
  async ready(t) {
    const c = Math.floor(Math.floor(t / this.meta.frame_dt) / this.meta.chunk_frames);
    this.load(c);
    await this.chunks.get(c)?.promise;
  }
}

/** Map vid -> index for frame b, cached on the frame object. */
function indexOf(frame) {
  if (!frame._idx) {
    frame._idx = new Map();
    for (let j = 0; j < frame.vid.length; j++) frame._idx.set(frame.vid[j], j);
  }
  return frame._idx;
}

/** Shortest-arc interpolation of compass angles (degrees). */
function lerpAngle(a, b, u) {
  const d = ((b - a + 540) % 360) - 180;
  return (a + d * u + 360) % 360;
}

/**
 * Vehicles between two frames as GeoJSON points, plus live statistics.
 * @param {object} a frame at or before the playback time
 * @param {object|null} b next frame (null at the end)
 * @param {number} u 0..1 position between a and b
 * @returns {{geojson: object, stats: {count:number, meanKmh:number, stoppedShare:number}}}
 */
export function interpolate(a, b, u) {
  const idx = b ? indexOf(b) : null;
  const features = new Array(a.vid.length);
  let vsum = 0;
  let stopped = 0;
  for (let i = 0; i < a.vid.length; i++) {
    const j = idx?.get(a.vid[i]);
    let lon = a.lon[i], lat = a.lat[i], hdg = a.hdg[i];
    if (j !== undefined) {
      lon += (b.lon[j] - lon) * u;
      lat += (b.lat[j] - lat) * u;
      hdg = lerpAngle(hdg, b.hdg[j], u);
    }
    vsum += a.speed[i];
    if (a.speed[i] < STOPPED_MS) stopped++;
    features[i] = { type: 'Feature', geometry: { type: 'Point', coordinates: [lon, lat] },
                    properties: { t: VEHICLE_TYPES[a.vtype[i]] ?? 'car', h: Math.round(hdg) } };
  }
  const n = a.vid.length;
  return {
    geojson: { type: 'FeatureCollection', features },
    stats: { count: n, meanKmh: n ? (vsum / n) * 3.6 : 0, stoppedShare: n ? stopped / n : 0 },
  };
}
