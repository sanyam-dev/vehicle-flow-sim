// Leaflet layers for the road network.
//
// Design: roads are neutral slate so the map stays calm; colour is reserved
// for information. A road turns amber / red only where traffic is slow or
// jammed (or everywhere, with the speed heatmap switched on).
//
// * Stacking uses one canvas pane per level: underpass (below, dark with
//   dashed blue edges), road, flyover (above, violet edges and a shadow).
// * Two-way roads are shifted sideways (towards the driving side) so each
//   direction is visible; widths follow lane counts at true scale (3.2 m/lane).
// * Junctions: signals show a stop bar per approach in the live light colour,
//   circles are drawn as an island, every junction carries its id (J#).
// * All mouse handling goes through one invisible "hit" pane on top, because
//   stacked canvases would otherwise hide each other's events.

import L from 'leaflet';
import { COLORS, LANE_M, byZoom, metresPerPixel, offsetLine, gridLines, speedColor,
         stopBar } from './mapgeom.js';

export { COLORS, speedColor } from './mapgeom.js';

const PANES = [['grid', 350], ['underpass', 380], ['road', 400], ['bridge', 410],
               ['nodes', 420], ['edit', 430], ['hit', 440]];
const GRADE_PANE = { bridge: 'bridge', underpass: 'underpass' };
const MAJOR = new Set(['motorway', 'trunk', 'primary']);
const MID = new Set(['secondary', 'tertiary']);
const JUNCTIONS = new Set(['signal', 'priority', 'roundabout']);
const QUEUE_COLORS = { 1: COLORS.slow, 2: COLORS.jam };
const LIGHTS = { red: COLORS.jam, amber: COLORS.slow, green: COLORS.free };
const SLOW_BELOW = 0.6; // speed ratio under which a road is tinted without the heatmap
const LABEL_ZOOM = 17; // junction ids appear once the user zooms in
const LABEL_GAP_PX = 44;
export const EDIT_COLORS = { flyover: COLORS.bridge, underpass: COLORS.underpass,
                             add_lane: COLORS.edit };

/** Tooltip text of a road. */
function roadTip(p) {
  const grade = p.grade === 'bridge' ? ' · flyover' : p.grade === 'underpass' ? ' · underpass' : '';
  return `<b>${p.name || 'Unnamed road'}</b><br>${p.road} · ${p.highway} · ${p.lanes} lane`
    + `${p.lanes > 1 ? 's' : ''}${grade}`;
}

/** Tooltip text of a junction. */
function nodeTip(p) {
  const kind = { signal: 'traffic signal', roundabout: 'circle', priority: 'give way' }[p.kind];
  return `<b>${p.label}</b> · ${kind}${p.stop_s ? ` · ${p.stop_s} s red` : ''}`;
}

/** Road network on a Leaflet map: build once per network, then restyle cheaply. */
export class NetworkLayer {
  /**
   * @param {L.Map} map
   * @param {{onEdge: (props: object, latlng: L.LatLng) => void,
   *          onNode: (props: object) => void}} handlers click callbacks
   */
  constructor(map, handlers) {
    this.map = map;
    this.handlers = handlers;
    this.r = {};
    for (const [name, z] of PANES) {
      const pane = map.createPane(`vf-${name}`);
      pane.style.zIndex = z;
      if (name !== 'hit') pane.style.pointerEvents = 'none';
      this.r[name] = L.canvas({ pane: `vf-${name}`, padding: 0.3, tolerance: 4 });
    }
    this.group = L.layerGroup().addTo(map);
    this.editGroup = L.layerGroup().addTo(map);
    this.labelGroup = L.layerGroup().addTo(map);
    this.barGroup = L.layerGroup().addTo(map);
    this.edges = new Map(); // edge id -> {p, line, casing, road, shadow, hit, r, closed, sel}
    this.nodes = new Map(); // node index -> {p, latlng, marker, island, q, edit}
    this.bars = []; // {bar, cycle, offset, amber, start, green, state}
    this.variant = 'baseline';
    this.heat = false;
    this.bounds = null;
    map.on('zoomend', () => this.restyle());
    map.on('moveend', () => this.#relabel(this.map.getZoom()));
  }

  /** Remove everything. */
  clear() {
    for (const g of [this.group, this.editGroup, this.labelGroup, this.barGroup]) g.clearLayers();
    this.edges.clear();
    this.nodes.clear();
    this.bars = [];
  }

  /**
   * Draw a network GeoJSON from the backend (edges + junction/boundary points).
   * @param {object} net FeatureCollection with `meta.bbox` and `meta.driving_side`
   */
  setNetwork(net) {
    this.clear();
    const sign = (net.meta?.driving_side ?? 'left') === 'left' ? 1 : -1;
    for (const pts of gridLines(net.meta.area_bbox ?? net.meta.bbox)) {
      L.polyline(pts, { renderer: this.r.grid, interactive: false, color: COLORS.grid, weight: 1 })
        .addTo(this.group);
    }
    const edges = net.features.filter((f) => f.properties.kind === 'edge');
    for (const f of edges) this.#addEdge(f, sign);
    for (const e of this.edges.values()) e.road.addTo(this.group); // roads above all casings
    for (const e of this.edges.values()) e.hit.addTo(this.group);
    for (const f of net.features) if (f.properties.kind !== 'edge') this.#addNode(f);
    this.restyle();
  }

  #addEdge(f, sign) {
    const p = f.properties;
    const d = p.two_way ? sign * (p.lanes * LANE_M / 2 + 0.3) : 0;
    const line = d ? offsetLine(f.geometry.coordinates, d)
                   : f.geometry.coordinates.map(([lon, lat]) => [lat, lon]);
    const opts = { renderer: this.r[GRADE_PANE[p.grade] ?? 'road'], interactive: false };
    const shadow = p.grade === 'bridge' ? L.polyline(line, opts).addTo(this.group) : null;
    // Added / widened roads get a halo behind the casing, so the casing can keep
    // its flyover / underpass colour while the change stays visible.
    const changed = p.added || p.lanes !== p.base_lanes;
    const halo = changed ? L.polyline(line, opts).addTo(this.group) : null;
    const casing = L.polyline(line, opts).addTo(this.group);
    const road = L.polyline(line, opts);
    const e = { p, line, casing, road, shadow, halo, r: null, closed: false, sel: false };
    e.hit = L.polyline(line, { renderer: this.r.hit, opacity: 0, bubblingMouseEvents: false })
      .bindTooltip(roadTip(p), { sticky: true, className: 'map-tip', direction: 'top', offset: [0, -6] })
      .on('click', (ev) => this.#visible(e) && this.handlers.onEdge?.(p, ev.latlng));
    this.edges.set(p.edge, e);
  }

  #addNode(f) {
    const p = f.properties;
    const [lon, lat] = f.geometry.coordinates;
    const opts = { renderer: this.r.nodes, interactive: false };
    const n = { p, latlng: L.latLng(lat, lon), q: -1, edit: false };
    n.island = L.circle(n.latlng, { ...opts, radius: p.ring_m ?? 8 })
      .addTo(this.group);
    n.marker = L.circleMarker(n.latlng, opts).addTo(this.group);
    if (JUNCTIONS.has(p.kind)) {
      L.circleMarker(n.latlng, { renderer: this.r.hit, radius: 10, opacity: 0, fillOpacity: 0,
                                 bubblingMouseEvents: false })
        .bindTooltip(nodeTip(p), { className: 'map-tip', direction: 'top', offset: [0, -8] })
        .on('click', () => this.handlers.onNode?.(p)).addTo(this.group);
    }
    this.nodes.set(p.node, n);
  }

  /** Is this edge part of the variant being shown? (added roads: scenario only) */
  #visible(e) {
    return !(this.variant === 'baseline' && e.p.added);
  }

  /** Lanes of an edge in the shown variant. */
  #lanes(e) {
    return this.variant === 'baseline' ? (e.p.base_lanes || e.p.lanes) : e.p.lanes;
  }

  /** Node kind in the shown variant. */
  #kind(n) {
    return this.variant === 'baseline' ? (n.p.base_kind ?? n.p.kind) : n.p.kind;
  }

  /** Re-apply every style (after zoom, a new network or a view switch). */
  restyle() {
    const z = this.map.getZoom();
    const mpp = metresPerPixel(z, this.map.getCenter().lat);
    for (const e of this.edges.values()) this.#styleEdge(e, z, mpp);
    for (const n of this.nodes.values()) this.#styleNode(n, z);
    for (const b of this.bars) b.bar.setStyle({ weight: byZoom(z, [[16.4, 0], [16.5, 2], [19, 5]]) });
    this.#relabel(z);
  }

  /** Road fill colour: neutral by class, tinted where slow (or always with the heatmap). */
  #fill(e) {
    if (e.closed) return COLORS.closed;
    if (e.r !== null && e.r !== undefined && (this.heat || e.r < SLOW_BELOW)) return speedColor(e.r);
    if (e.p.grade === 'underpass') return COLORS.roadUnder;
    return MAJOR.has(e.p.highway) ? COLORS.roadMajor : MID.has(e.p.highway) ? COLORS.roadMid
      : COLORS.roadMinor;
  }

  /** Casing (outline) style: selection, grade, or plain (scenario changes use the halo). */
  #casing(e, w) {
    if (e.sel) return { color: COLORS.select, weight: w + 6, opacity: 0.7, dashArray: null };
    if (e.p.grade === 'bridge') return { color: COLORS.bridge, weight: w + 4, opacity: 1, dashArray: null };
    if (e.p.grade === 'underpass') {
      return { color: COLORS.underpass, weight: w + 4, opacity: 0.9, dashArray: '5 5' };
    }
    return { color: COLORS.casing, weight: w + 2, opacity: 1, dashArray: null };
  }

  #styleEdge(e, z = this.map.getZoom(), mpp = metresPerPixel(z, this.map.getCenter().lat)) {
    if (!this.#visible(e)) {
      for (const l of [e.casing, e.road, e.shadow, e.halo]) l?.setStyle({ opacity: 0 });
      return;
    }
    const minor = !MAJOR.has(e.p.highway) && !MID.has(e.p.highway);
    const w = Math.max(minor ? 0.8 : 1.6, (this.#lanes(e) * LANE_M) / mpp);
    e.shadow?.setStyle({ color: '#000', weight: w + 10, opacity: 0.35, lineCap: 'butt' });
    e.halo?.setStyle({ color: COLORS.edit, weight: w + 9, lineCap: 'butt',
                       opacity: this.variant === 'scenario' ? 0.9 : 0 });
    e.casing.setStyle({ ...this.#casing(e, w), lineCap: 'butt' });
    e.road.setStyle({ color: this.#fill(e), weight: w, opacity: 1, lineCap: 'butt',
                      dashArray: e.closed ? '4 4' : null });
    e.hit.setStyle({ weight: Math.max(w + 4, 12) });
  }

  #styleNode(n, z = this.map.getZoom()) {
    const kind = this.#kind(n);
    const hide = { opacity: 0, fillOpacity: 0 };
    const edit = n.edit ? { color: COLORS.edit, weight: 2.5 } : null;
    // The stroke is the ring road itself (one lane wide), the fill is the island.
    const mpp = metresPerPixel(z, this.map.getCenter().lat);
    n.island.setStyle(kind === 'roundabout'
      ? { color: edit?.color ?? COLORS.roadMajor, weight: Math.max(LANE_M / mpp, 2), opacity: 1,
          fillColor: COLORS.island, fillOpacity: 1 } : hide);
    if (kind === 'boundary') {
      n.marker.setStyle({ radius: 2.5, color: COLORS.boundary, weight: 1, opacity: 1,
                          fillColor: COLORS.bg, fillOpacity: 1 });
    } else if (kind === 'signal' || kind === 'priority') {
      const signal = kind === 'signal';
      n.marker.setStyle({ radius: signal ? byZoom(z, [[13, 2], [17, 5]]) : byZoom(z, [[13, 1], [17, 3]]),
                          color: COLORS.bg, weight: 1.5, opacity: 1, fillOpacity: 1, ...edit,
                          fillColor: QUEUE_COLORS[n.q] ?? (signal ? COLORS.light : COLORS.junction) });
    } else {
      n.marker.setStyle(kind === 'roundabout' && n.q > 0
        ? { radius: 3, color: COLORS.bg, weight: 1, opacity: 1, fillColor: QUEUE_COLORS[n.q],
            fillOpacity: 1 } : hide);
    }
  }

  /** Junction id labels for the junctions in view, thinned so they never overlap. */
  #relabel(z) {
    this.labelGroup.clearLayers();
    if (z < LABEL_ZOOM) return;
    const placed = [];
    const rank = (n) => (this.#kind(n) === 'priority' ? 1 : 0);
    const view = this.map.getBounds().pad(0.1);
    const nodes = [...this.nodes.values()]
      .filter((n) => JUNCTIONS.has(this.#kind(n)) && view.contains(n.latlng))
      .sort((a, b) => rank(a) - rank(b));
    for (const n of nodes) {
      const pt = this.map.latLngToLayerPoint(n.latlng);
      if (placed.some((q) => Math.abs(q.x - pt.x) < LABEL_GAP_PX && Math.abs(q.y - pt.y) < 14)) continue;
      placed.push(pt);
      L.tooltip({ permanent: true, direction: 'right', offset: [7, 0], className: 'jlabel' })
        .setLatLng(n.latlng).setContent(n.p.label).addTo(this.labelGroup);
    }
  }

  /**
   * Draw one stop bar per signalised approach.
   * @param {object[]|null} signals result.signals[variant]: controllers with phases
   */
  setSignals(signals) {
    this.barGroup.clearLayers();
    this.bars = [];
    for (const c of signals ?? []) {
      for (const ph of c.phases) {
        for (const id of ph.edges) {
          const e = this.edges.get(id);
          if (!e) continue;
          const bar = L.polyline(stopBar(e.line, (e.p.lanes * LANE_M) / 2),
                                 { renderer: this.r.nodes, interactive: false, lineCap: 'round' })
            .addTo(this.barGroup);
          this.bars.push({ bar, cycle: c.cycle, offset: c.offset, amber: c.amber,
                           start: ph.start, green: ph.green, state: '' });
        }
      }
    }
    this.restyle();
  }

  /** Colour every stop bar for simulation time t (seconds). */
  updateSignals(t) {
    for (const b of this.bars) {
      const into = ((t + b.offset) % b.cycle) - b.start;
      const state = into < 0 ? 'red' : into < b.green ? 'green' : into < b.green + b.amber ? 'amber' : 'red';
      if (state !== b.state) {
        b.state = state;
        b.bar.setStyle({ color: LIGHTS[state] });
      }
    }
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

  /** Show 'baseline' or 'scenario' roads, lanes and junction controls. */
  setVariant(variant) {
    if (variant !== this.variant) { this.variant = variant; this.restyle(); }
  }

  /** Full speed heatmap on / off. */
  setHeat(on) {
    if (on !== this.heat) { this.heat = on; this.restyle(); }
  }

  /** Outline junctions that have a pending edit. @param {Set<string>} ids node `osm` ids */
  setNodeEdits(ids) {
    for (const n of this.nodes.values()) {
      const edit = ids.has(n.p.osm);
      if (edit !== n.edit) { n.edit = edit; this.#styleNode(n); }
    }
  }

  /**
   * Draw pending map edits and the edit being drawn.
   * @param {object[]} edits {kind, a: {lat, lon}, b: {lat, lon}}
   * @param {{a: object|null, b: object|null}} draft points picked so far
   * @param {object|null} hover snapped point under the cursor
   */
  setPreviews(edits, draft, hover) {
    this.editGroup.clearLayers();
    const opts = { renderer: this.r.edit, interactive: false };
    const pin = (pt, color) => L.circleMarker([pt.lat, pt.lon], { ...opts, radius: 5, color: COLORS.bg,
      weight: 2, fillColor: color, fillOpacity: 1 }).addTo(this.editGroup);
    const link = (a, b, color) => L.polyline([[a.lat, a.lon], [b.lat, b.lon]], { ...opts, color,
      weight: 3, dashArray: '8 6' }).addTo(this.editGroup);
    for (const e of edits) {
      link(e.a, e.b, EDIT_COLORS[e.kind]);
      pin(e.a, EDIT_COLORS[e.kind]);
      pin(e.b, EDIT_COLORS[e.kind]);
    }
    if (draft.a && (draft.b || hover)) link(draft.a, draft.b ?? hover, COLORS.select);
    for (const pt of [draft.a, draft.b]) if (pt) pin(pt, COLORS.select);
    if (hover) {
      L.circleMarker([hover.lat, hover.lon], { ...opts, radius: 8, color: COLORS.select, weight: 2,
                                               fillOpacity: 0 }).addTo(this.editGroup);
    }
  }

  /** Closest point of an edge to a layer point: {c: L.Point, d: pixels}. */
  #closest(e, at) {
    const pts = e.line.map((ll) => this.map.latLngToLayerPoint(ll));
    let best = { c: pts[0], d: Infinity };
    for (let i = 1; i < pts.length; i++) {
      const c = L.LineUtil.closestPointOnSegment(at, pts[i - 1], pts[i]);
      const d = c.distanceTo(at);
      if (d < best.d) best = { c, d };
    }
    return best;
  }

  /**
   * Roads a clicked point can snap to, for the map editor.
   *
   * One option per stacked road: the nearest ground road, then each flyover
   * and each underpass road within reach. The ground road comes first, so it
   * is the default. Roads a scenario added are skipped (the backend resolves
   * points before any new road exists).
   * @param {L.LatLng} latlng clicked point
   * @param {number} maxPx ignore roads further than this many screen pixels
   * @param {number} stackM other levels count as stacked within this many metres
   * @returns {{lat: number, lon: number, name: string, grade: string, uvk: string,
   *            id: number}[]} options, default first ([] when no road is near)
   */
  roadsAt(latlng, maxPx = 30, stackM = 40) {
    const at = this.map.latLngToLayerPoint(latlng);
    const best = new Map(); // road key -> {c, d, e}
    for (const e of this.edges.values()) {
      if (e.p.added) continue;
      const hit = this.#closest(e, at);
      const key = e.p.grade === 'road' ? 'road' : `${e.p.grade}|${e.p.name || e.p.road}`;
      if (hit.d <= maxPx && !(best.get(key)?.d <= hit.d)) best.set(key, { ...hit, e });
    }
    const nearest = Math.min(...[...best.values()].map((h) => h.d));
    const rank = { road: 0, bridge: 1, underpass: 2 };
    return [...best.values()]
      .map(({ c, d, e }) => {
        const ll = this.map.layerPointToLatLng(c);
        return { d, m: this.map.distance(latlng, ll), lat: ll.lat, lon: ll.lng,
                 name: e.p.name || e.p.road, grade: e.p.grade, uvk: e.p.uvk, id: e.p.edge };
      })
      .filter((o) => o.d === nearest || o.m <= stackM)
      .sort((a, b) => rank[a.grade] - rank[b.grade] || a.d - b.d)
      .slice(0, 4)
      .map(({ d, m, ...o }) => o);
  }

  /**
   * Fit the area and lock the view to it: no panning away, no zooming out past it.
   * @param {[number, number, number, number]} bbox west, south, east, north
   */
  lock([w, s, e, n]) {
    this.bounds = L.latLngBounds([[s, w], [n, e]]);
    this.fitted = false;
    this.relock(true);
  }

  /** Recompute the zoom limit (call after a resize); `fit` also resets the view. */
  relock(fit = false) {
    if (!this.bounds) return;
    this.map.invalidateSize({ pan: false }); // the container may have been laid out since
    const size = this.map.getSize();
    if (size.x < 50 || size.y < 50) return; // not visible yet: the resize observer calls again
    this.map.setMinZoom(0);
    const z = this.map.getBoundsZoom(this.bounds, false, [16, 16]);
    this.map.setMaxBounds(this.bounds.pad(0.04));
    if (fit || !this.fitted || this.map.getZoom() < z) {
      this.map.fitBounds(this.bounds, { padding: [16, 16], animate: false });
    }
    this.fitted = true;
    this.map.setMinZoom(z);
  }

  /** Centre the view on a junction. @param {number} node node index */
  focusNode(node) {
    const n = this.nodes.get(node);
    if (n) this.map.setView(n.latlng, Math.max(this.map.getZoom(), 17), { animate: false });
  }
}
