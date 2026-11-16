// Run lifecycle: submit a RunRequest, poll status, load network + frames.

import { api, isDemo, DEMO_RUN_ID } from './api.js';
import { FrameSource } from './frames.js';
import { player } from './player.svelte.js';
import { app, resetLive } from './store.svelte.js';

const POLL_MS = 1000;
// Backstop: a running run whose stage/progress has not changed for this long
// is reported as stuck (the backend's own time limits should end it first).
const STALL_MS = 10 * 60 * 1000;
let pollToken = 0;

/** Remember the run in the URL hash so a reload reopens it. */
function setHash(id) {
  history.replaceState(null, '', id && !isDemo() ? `#run=${id}` : location.pathname);
}

/** Run id from `#run=<id>`, if any. */
export function runFromHash() {
  return /#run=([a-z0-9]+)/.exec(location.hash)?.[1] ?? null;
}

/**
 * A clicked point as sent to the backend: the snapped position plus the road
 * the user chose there (`grade` and edge "u-v-k"), so stacked roads resolve
 * to the right level. Accepts both a map point (`uvk`) and an echoed request (`edge`).
 */
const pointBody = (p) => ({ lat: p.lat, lon: p.lon, grade: p.grade ?? null,
                            edge: p.uvk ?? p.edge ?? null });

/** A map edit as sent to the backend (drops UI-only fields such as road names). */
const mapEditBody = ({ kind, a, b, lanes, both_directions }) => (
  { kind, lanes, both_directions, a: pointBody(a), b: pointBody(b) });

/** A junction edit as sent to the backend. */
const junctionEditBody = ({ node, control, stop_s }) => (
  { node: String(node), control, stop_s: control === 'signal' ? stop_s : null });

/**
 * Build the RunRequest body from form params + pending scenario edits.
 * @param {object} params place/traffic/time/duration_s/warmup_s/seed/dt
 */
export function buildRequest(params) {
  const e = app.edits;
  return { ...params, close_edges: [...e.close_edges], close_roads: [...e.close_roads],
           close_lanes: e.close_lanes.map((c) => ({ road: c.road, lanes: [...c.lanes] })),
           map_edits: e.map_edits.map(mapEditBody),
           junction_edits: e.junction_edits.map(junctionEditBody) };
}

/**
 * Pending edits that the loaded run does not include yet.
 *
 * Edits that the run already applied are drawn from the run's own network
 * (the snapped flyover, the closed edge, the new circle), so the map must not
 * also draw their pending preview on top.
 * @returns {{close_edges: string[], map_edits: object[], junction_edits: object[]}}
 */
export function unappliedEdits() {
  const run = app.request ?? {};
  const e = app.edits;
  const notIn = (list, body = (x) => x) => {
    const done = new Set((list ?? []).map((x) => JSON.stringify(body(x))));
    return (x) => !done.has(JSON.stringify(body(x)));
  };
  return { close_edges: e.close_edges.filter(notIn(run.close_edges)),
           map_edits: e.map_edits.filter(notIn(run.map_edits, mapEditBody)),
           junction_edits: e.junction_edits.filter(notIn(run.junction_edits, junctionEditBody)) };
}

/** Show the loaded run's parameters in the form. */
function syncParams(req) {
  for (const k of Object.keys(app.params)) if (req[k] !== undefined && req[k] !== null) app.params[k] = req[k];
}

/** Submit a new run and start polling it. */
export async function startRun(request) {
  if (isDemo()) {
    app.error = 'Backend offline: this deployment replays a recorded demo run.';
    return;
  }
  player.detach();
  resetLive();
  Object.assign(app.live, { count: 0, meanKmh: 0, stoppedShare: 0, minute: null });
  Object.assign(app, { status: 'queued', stage: 'submitting', progress: 0, error: '',
                       result: null, network: null, request, selected: null,
                       selectedNode: null, editMode: false, draft: { a: null, b: null }, choice: null,
                       startedAt: Date.now() });
  try {
    const { id } = await api.createRun(request);
    app.runId = id;
    setHash(id);
    poll(id);
  } catch (err) {
    const msg = err.status === 503
      ? `Simulator busy (all workers running). Retry in ${err.retryAfter ?? 30}s.`
      : err.message;
    Object.assign(app, { status: 'error', error: msg });
  }
}

/**
 * Watch for a running run that stops changing.
 * @returns {(run: object) => boolean} call once per poll; true when stalled
 */
function stallWatch() {
  let key = '';
  let since = Date.now();
  return (run) => {
    const k = `${run.state}|${run.stage}|${run.progress}`;
    if (k !== key || run.state !== 'running') {
      key = k;
      since = Date.now();
      return false;
    }
    return Date.now() - since > STALL_MS;
  };
}

/**
 * Poll a run until it finishes, loading the network as soon as it exists.
 * @param {string} id run id
 * @param {{forgetIfMissing?: boolean}} [opts] when true, a 404 (run gone,
 *   e.g. after a server restart wiped the run store) quietly resets the page
 *   instead of showing an error; used when restoring a run from the URL.
 */
export async function poll(id, { forgetIfMissing = false } = {}) {
  const token = ++pollToken;
  const stalled = stallWatch();
  while (token === pollToken) {
    let run;
    try {
      run = await api.run(id);
    } catch (err) {
      if (forgetIfMissing && err.status === 404) {
        setHash(null);
        Object.assign(app, { runId: null, status: 'idle', stage: '', error: '' });
        return;
      }
      Object.assign(app, { status: 'error', error: err.message });
      return;
    }
    Object.assign(app, { status: run.state, stage: run.stage ?? '', progress: run.progress ?? 0,
                         error: run.error ?? '', request: run.request });
    if (run.request && app.status === 'done') syncParams(run.request);
    if (run.has_network && !app.network) {
      const net = await api.network(id);
      if (token !== pollToken) return; // a newer run started while this was loading
      app.network = net;
    }
    if (run.state === 'done') return finish(id, run.result);
    if (run.state === 'error') return;
    if (stalled(run)) {
      Object.assign(app, { status: 'error',
                           error: 'No progress for 10 minutes; the run looks stuck. Please start it again.' });
      return;
    }
    await new Promise((r) => setTimeout(r, POLL_MS));
  }
}

/**
 * Load result + frames of a finished run and start the replay.
 *
 * A run with edits opens on the scenario, so the edited network and the
 * re-routed vehicles are what the user sees first (baseline is one click away).
 */
async function finish(id, result) {
  app.result = result;
  const variant = result?.scenario ? 'scenario' : 'baseline';
  app.variant = variant;
  await attachVariant(variant, false);
  player.play();
}

/** Switch the replay between 'baseline' and 'scenario', keeping the clock. */
export async function attachVariant(variant, keepTime = true) {
  const meta = await api.meta(app.runId, variant);
  app.variant = variant;
  resetLive();
  await player.attach(new FrameSource(app.runId, variant, meta), keepTime);
}

/** Open an existing run (URL hash) or the bundled demo run. */
export async function openRun(id) {
  pollToken++;
  player.detach();
  Object.assign(app, { runId: id, network: null, result: null, status: 'queued', error: '',
                       startedAt: Date.now() });
  setHash(id);
  await poll(id, { forgetIfMissing: true });
}

/** Open the demo run shipped with the static build. */
export const openDemo = () => openRun(DEMO_RUN_ID);
