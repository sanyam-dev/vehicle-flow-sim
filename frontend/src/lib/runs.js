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
 * Build the RunRequest body from form params + pending scenario edits.
 * @param {object} params place/traffic/time/duration_s/warmup_s/seed/dt
 */
export function buildRequest(params) {
  const e = app.edits;
  return { ...params, close_edges: [...e.close_edges], close_roads: [...e.close_roads],
           close_lanes: e.close_lanes.map((c) => ({ road: c.road, lanes: [...c.lanes] })) };
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

/** Poll a run until it finishes, loading the network as soon as it exists. */
export async function poll(id) {
  const token = ++pollToken;
  const stalled = stallWatch();
  while (token === pollToken) {
    let run;
    try {
      run = await api.run(id);
    } catch (err) {
      Object.assign(app, { status: 'error', error: err.message });
      return;
    }
    Object.assign(app, { status: run.state, stage: run.stage ?? '', progress: run.progress ?? 0,
                         error: run.error ?? '', request: run.request });
    if (run.request && app.status === 'done') syncParams(run.request);
    if (run.has_network && !app.network) app.network = await api.network(id);
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

/** Load result + frames of a finished run and start the replay. */
async function finish(id, result) {
  app.result = result;
  app.variant = 'baseline';
  await attachVariant('baseline', false);
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
  await poll(id);
}

/** Open the demo run shipped with the static build. */
export const openDemo = () => openRun(DEMO_RUN_ID);
