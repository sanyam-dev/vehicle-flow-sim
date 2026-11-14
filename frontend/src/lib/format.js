// Small formatting and metric helpers shared by components.

/** Seconds -> "HH:MM:SS". */
export function hms(s) {
  const t = Math.max(0, Math.floor(s));
  const p = (n) => String(n).padStart(2, '0');
  return `${p(Math.floor(t / 3600))}:${p(Math.floor((t % 3600) / 60))}:${p(t % 60)}`;
}

/** Clock time of day: start "HH:MM" plus elapsed seconds -> "HH:MM". */
export function clockAt(start, elapsed) {
  const [h, m] = (start ?? '00:00').split(':').map(Number);
  const mins = Math.floor(h * 60 + m + elapsed / 60) % 1440;
  return `${String(Math.floor(mins / 60)).padStart(2, '0')}:${String(mins % 60).padStart(2, '0')}`;
}

/** Thousands separator, fixed decimals; '—' for null. */
export function num(v, digits = 0) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return Number(v).toLocaleString('en-US', { minimumFractionDigits: digits,
                                             maximumFractionDigits: digits });
}

/** Congestion label for a stopped-vehicle share (0..1). */
export function congestionLabel(share) {
  if (share < 0.1) return 'Free';
  if (share < 0.25) return 'Light';
  if (share < 0.45) return 'Moderate';
  if (share < 0.7) return 'Heavy';
  return 'Gridlock';
}

/** KPI display metadata: label, unit, and whether lower is better. */
export const KPI_INFO = {
  mean_travel_time_s: ['Mean travel time', 's', true],
  p90_travel_time_s: ['P90 travel time', 's', true],
  mean_delay_s: ['Mean delay', 's', true],
  throughput_vph: ['Throughput', 'veh/h', false],
  mean_speed_kmh: ['Mean speed', 'km/h', false],
  mean_queue_veh: ['Mean queue', 'veh', true],
  max_queue_veh: ['Max queue', 'veh', true],
  trips_completed: ['Trips completed', '', false],
  gridlock_removed: ['Gridlock removals', '', true],
  overlaps: ['Vehicle overlaps', '', true],
  hard_brakes: ['Hard-braking events', '', true],
  unserved_trips: ['Unserved trips', '', true],
};

/**
 * Index of the metrics minute bin for replay time t, or -1 during warm-up.
 * @param {object} summary one variant's summary (has series.minute)
 * @param {number} t seconds since run start
 */
export function minuteIndex(summary, t) {
  const minutes = summary?.series?.minute ?? [];
  return minutes.indexOf(Math.floor(t / 60));
}
