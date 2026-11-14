// Shared reactive application state (Svelte 5 runes).

/** Default run parameters (mirror backend RunRequest defaults). */
export const DEFAULT_PARAMS = {
  place: 'Silk Board Junction, Bengaluru',
  traffic: 'moderate',
  time: '09:00',
  duration_s: 900,
  warmup_s: 180,
  seed: 42,
  dt: 0.5,
  signal_stop_s: 45,
};

/** Empty set of scenario edits. */
export const noEdits = () => ({ close_edges: [], close_roads: [], close_lanes: [],
                                map_edits: [], junction_edits: [] });

const HISTORY = 90; // samples kept for sparklines

export const app = $state({
  backend: 'checking', // 'checking' | 'live' | 'demo'
  runId: null,
  status: 'idle', // 'idle' | 'queued' | 'running' | 'done' | 'error'
  stage: '',
  startedAt: null, // Date.now() when the current run was submitted/opened
  progress: 0,
  error: '',
  request: null, // RunRequest of the loaded run
  result: null, // run_pair() output
  network: null, // GeoJSON FeatureCollection
  variant: 'baseline', // which recorded run the map replays
  params: { ...DEFAULT_PARAMS },
  edits: noEdits(),
  selected: null, // clicked road edge properties
  selectedNode: null, // clicked junction properties
  focusNode: null, // node index the map should bring into view
  editMode: false, // pencil tool: the next two map clicks pick points
  draft: { a: null, b: null }, // points picked so far: {lat, lon, name}
  notice: '', // one-line message under the form (e.g. edits were cleared)
  satellite: false,
  heat: false, // full speed heatmap instead of "only where slow"
  chat: [{ role: 'assistant', content: 'What should we simulate?' }],
  chatBusy: false,
  proposal: null, // RunRequest proposed by the chat agent
  live: { count: 0, meanKmh: 0, stoppedShare: 0, minute: null,
          hist: { speed: [], count: [], queue: [], thr: [] } },
});

/** Push live stats into the sparkline history (bounded). */
export function pushLive(stats, queue, thr) {
  Object.assign(app.live, stats);
  const h = app.live.hist;
  for (const [k, v] of [['speed', stats.meanKmh], ['count', stats.count],
                        ['queue', queue], ['thr', thr]]) {
    h[k].push(v ?? null);
    if (h[k].length > HISTORY) h[k].shift();
  }
}

/** Clear sparkline history (on seek / variant switch / new run). */
export function resetLive() {
  for (const k of Object.keys(app.live.hist)) app.live.hist[k] = [];
}

/** Number of pending scenario edits. */
export function editCount() {
  const e = app.edits;
  return e.close_edges.length + e.close_roads.length + e.close_lanes.length
    + e.map_edits.length + e.junction_edits.length;
}
