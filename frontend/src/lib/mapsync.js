// Keeps the map in sync with replay time: vehicles every animation frame
// (throttled), road speeds and junction queues once per simulated minute,
// sparkline history once per simulated second.

import { app, pushLive } from './store.svelte.js';
import { minuteIndex } from './format.js';

const VEHICLE_MS = 40; // max vehicle redraw rate (~25 fps)

/** Queue length -> colour class 0 (free) / 1 (slow) / 2 (jammed). */
function queueClass(q) {
  if (q === null || q === undefined) return -1;
  return q < 3 ? 0 : q < 10 ? 1 : 2;
}

export class MapSync {
  /**
   * @param {import('./maplayers.js').NetworkLayer} net road network layer
   * @param {object} veh VehicleLayer
   */
  constructor(net, veh) {
    this.net = net;
    this.veh = veh;
    this.lastDraw = 0;
    this.lastMinute = undefined;
    this.lastSecond = -1;
    this.nodeByLabel = new Map();
    this.edges = [];
  }

  /** Index the network (call when a new network is loaded). */
  setNetwork(net) {
    this.nodeByLabel.clear();
    this.edges = [];
    for (const f of net.features) {
      const p = f.properties;
      if (p.kind === 'edge') this.edges.push(p);
      else this.nodeByLabel.set(p.label, p.node);
    }
    this.lastMinute = undefined;
  }

  /** Renderer callback for the player. */
  render(geojson, stats, t) {
    const now = performance.now();
    if (now - this.lastDraw >= VEHICLE_MS) {
      this.veh.setData(geojson);
      this.lastDraw = now;
    }
    const summary = app.result?.[app.variant];
    const mi = minuteIndex(summary, t);
    if (mi !== this.lastMinute) this.applyMinute(summary, mi);
    const sec = Math.floor(t);
    if (sec !== this.lastSecond) {
      this.lastSecond = sec;
      const series = summary?.series;
      pushLive(stats, mi >= 0 ? series.queue_total[mi] : null,
               mi >= 0 ? series.throughput_per_min[mi] * 60 : null);
      app.live.minute = mi;
    }
  }

  /** Colour roads by speed ratio and junctions by queue for minute bin mi. */
  applyMinute(summary, mi) {
    this.lastMinute = mi;
    const row = mi >= 0 ? summary?.speed_heatmap?.kmh?.[mi] : null;
    for (const e of this.edges) {
      const kmh = row?.[e.edge];
      const r = kmh === null || kmh === undefined ? null : Math.min(kmh / Math.max(e.vmax_kmh, 1), 1);
      this.net.setSpeed(e.edge, r);
    }
    const queues = summary?.queue_by_junction ?? {};
    for (const [label, node] of this.nodeByLabel) {
      const q = mi >= 0 ? queues[label]?.[mi] : null;
      this.net.setQueue(node, queueClass(q));
    }
  }

  /** Mark closed edges: pending edits always, run closures in the scenario view. */
  applyClosures(pendingUvk, showRunClosures) {
    const pending = new Set(pendingUvk);
    for (const e of this.edges) {
      const closed = pending.has(e.uvk) || (showRunClosures && e.closed);
      this.net.setClosed(e.edge, closed);
    }
  }

  /** Highlight one edge (or none). */
  select(edgeId, prevId) {
    if (prevId !== null && prevId !== undefined) this.net.select(prevId, false);
    if (edgeId !== null && edgeId !== undefined) this.net.select(edgeId, true);
  }
}
