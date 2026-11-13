// Replay clock: advances simulated time with requestAnimationFrame and
// asks the registered renderer to draw the interpolated state.

import { interpolate } from './frames.js';

class Player {
  t = $state(0); // seconds since run start
  playing = $state(false);
  speed = $state(1); // replay speed multiplier
  duration = $state(0);
  buffering = $state(false);

  source = null; // FrameSource being replayed
  renderer = null; // (geojson, stats, t) => void
  #raf = 0;
  #last = 0;

  /** Attach a FrameSource and rewind (keeps time when `keepTime`). */
  async attach(source, keepTime = false) {
    this.source = source;
    this.duration = source.duration;
    if (!keepTime) this.t = 0;
    this.t = Math.min(this.t, this.duration);
    await source.ready(this.t);
    this.draw();
  }

  detach() {
    this.pause();
    this.source = null;
    this.duration = 0;
    this.t = 0;
  }

  play() {
    if (!this.source || this.playing) return;
    if (this.t >= this.duration - 1e-6) this.t = 0;
    this.playing = true;
    this.#last = performance.now();
    this.#raf = requestAnimationFrame((now) => this.#tick(now));
  }

  pause() {
    this.playing = false;
    this.buffering = false;
    cancelAnimationFrame(this.#raf);
  }

  toggle() {
    this.playing ? this.pause() : this.play();
  }

  /** Jump to time t (seconds) and redraw once loaded. */
  async seek(t) {
    if (!this.source) return;
    this.t = Math.max(0, Math.min(t, this.duration));
    this.draw();
    await this.source.ready(this.t);
    this.draw();
  }

  /** Advance by one recorded frame (paused stepping). */
  step() {
    this.pause();
    if (this.source) this.seek(Math.floor(this.t / this.source.meta.frame_dt + 1) * this.source.meta.frame_dt);
  }

  /** Draw the state at the current time; returns false if data is not loaded yet. */
  draw() {
    const src = this.source;
    if (!src || !this.renderer) return false;
    const pos = this.t / src.meta.frame_dt;
    const f = Math.min(Math.floor(pos), src.meta.n_frames - 1);
    const a = src.frame(f);
    if (!a) return false;
    const { geojson, stats } = interpolate(a, src.frame(f + 1), Math.min(pos - f, 1));
    this.renderer(geojson, stats, this.t);
    return true;
  }

  #tick(now) {
    if (!this.playing) return;
    const dt = Math.min((now - this.#last) / 1000, 0.25);
    this.#last = now;
    const ok = this.draw();
    this.buffering = !ok;
    if (ok) this.t = Math.min(this.t + dt * this.speed, this.duration);
    if (this.t >= this.duration) return this.pause();
    this.#raf = requestAnimationFrame((n) => this.#tick(n));
  }
}

export const player = new Player();
