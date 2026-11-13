// Vehicles drawn on one canvas over the Leaflet map (thousands per frame are
// too many for individual Leaflet markers). Each vehicle is a pre-rendered
// icon stamped at its position, rotated to its compass heading; icon size
// grows with zoom. The canvas ignores the mouse so roads stay clickable.

import L from 'leaflet';
import { vehicleIcons } from './icons.js';

/** Icon scale (1 = icon pixels) per zoom; between stops the scale grows geometrically. */
const SIZE_STOPS = [[13, 0.22], [16, 0.5], [18, 0.9], [20, 2.6]];

/** Icon scale at zoom z (log-linear between SIZE_STOPS). */
function iconScale(z) {
  if (z <= SIZE_STOPS[0][0]) return SIZE_STOPS[0][1];
  for (let i = 1; i < SIZE_STOPS.length; i++) {
    const [z0, s0] = SIZE_STOPS[i - 1];
    const [z1, s1] = SIZE_STOPS[i];
    if (z <= z1) return s0 * (s1 / s0) ** ((z - z0) / (z1 - z0));
  }
  return SIZE_STOPS.at(-1)[1];
}

export const VehicleLayer = L.Layer.extend({
  initialize() {
    this._features = [];
  },

  /** Create the canvas above the map panes and below the controls. */
  onAdd(map) {
    this._icons ??= vehicleIcons();
    const c = (this._canvas = L.DomUtil.create('canvas', 'vehicle-layer'));
    Object.assign(c.style, { position: 'absolute', left: 0, top: 0, zIndex: 450,
                             pointerEvents: 'none' });
    map.getContainer().appendChild(c);
    map.on('move zoom viewreset resize', this._redraw, this);
    this._redraw();
  },

  onRemove(map) {
    map.off('move zoom viewreset resize', this._redraw, this);
    this._canvas.remove();
  },

  /**
   * Replace the vehicles.
   * @param {{features: object[]}} geojson points with properties {t: type, h: heading deg}
   */
  setData(geojson) {
    this._features = geojson.features;
    this._redraw();
  },

  /** Match the canvas to the map size (device pixels). */
  _fit() {
    const size = this._map.getSize();
    const dpr = window.devicePixelRatio || 1;
    const c = this._canvas;
    if (c.width !== Math.round(size.x * dpr) || c.height !== Math.round(size.y * dpr)) {
      c.width = Math.round(size.x * dpr);
      c.height = Math.round(size.y * dpr);
      c.style.width = `${size.x}px`;
      c.style.height = `${size.y}px`;
    }
    return { dpr, size };
  },

  _redraw() {
    if (!this._map) return;
    const { dpr, size } = this._fit();
    const ctx = this._canvas.getContext('2d');
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, this._canvas.width, this._canvas.height);
    const k = iconScale(this._map.getZoom());
    for (const f of this._features) {
      const [lon, lat] = f.geometry.coordinates;
      const p = this._map.latLngToContainerPoint([lat, lon]);
      if (p.x < -40 || p.y < -40 || p.x > size.x + 40 || p.y > size.y + 40) continue;
      const icon = this._icons[f.properties.t] ?? this._icons.car;
      const a = (f.properties.h * Math.PI) / 180;
      const [cos, sin] = [Math.cos(a) * dpr, Math.sin(a) * dpr];
      ctx.setTransform(cos, sin, -sin, cos, p.x * dpr, p.y * dpr);
      ctx.drawImage(icon.canvas, (-icon.w * k) / 2, (-icon.h * k) / 2, icon.w * k, icon.h * k);
    }
  },
});
