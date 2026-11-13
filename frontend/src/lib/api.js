// HTTP client for the FastAPI backend, with a static "demo" fallback.
//
// Live mode:  {BASE}/api/runs/{id}/...           (FastAPI)
// Demo mode:  /demo/runs/{id}/....json            (pre-recorded files in public/)
//
// Every request carries an X-Session-Id header (a UUID kept in localStorage)
// so the backend gives each browser its own worker process.

const BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');
const SESSION_KEY = 'vfsim.session';
export const DEMO_RUN_ID = 'demo';

let demo = false;

/** Random UUID, with a fallback for non-secure contexts. */
function uuid() {
  if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });
}

/** Stable per-browser session id (falls back to a per-tab id if storage is blocked). */
export const sessionId = (() => {
  let id = null;
  try {
    id = localStorage.getItem(SESSION_KEY);
    if (!id) localStorage.setItem(SESSION_KEY, (id = uuid()));
  } catch {
    id = uuid();
  }
  return id;
})();

/** True when the app replays bundled demo files instead of calling the API. */
export const isDemo = () => demo;

/**
 * Probe the backend once at start-up; switch to demo mode if it is unreachable.
 * @returns {Promise<boolean>} true when a live backend answered.
 */
export async function detectBackend() {
  try {
    const res = await fetch(`${BASE}/api/health`, { signal: AbortSignal.timeout(3000) });
    demo = !(res.ok && (await res.json()).ok);
  } catch {
    demo = true;
  }
  return !demo;
}

/** Build the URL for an API path such as `/runs/abc/network`. */
function url(path) {
  return demo ? `/demo${path}.json` : `${BASE}/api${path}`;
}

/**
 * Fetch JSON with the session header; throws an Error carrying `status`.
 * @param {string} path API path starting with '/'.
 * @param {object} [body] JSON body; when given the request is a POST.
 */
async function request(path, body) {
  const init = { headers: { 'X-Session-Id': sessionId } };
  if (body !== undefined) {
    init.method = 'POST';
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  const res = await fetch(url(path), init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail);
    } catch { /* non-JSON error body */ }
    const err = new Error(detail || `HTTP ${res.status}`);
    err.status = res.status;
    err.retryAfter = Number(res.headers.get('Retry-After')) || null;
    throw err;
  }
  return res.json();
}

export const api = {
  /**
   * @param {{role:string, content:string}[]} messages full chat history
   * @param {object} [params] current RunRequest form values (agent context)
   */
  chat: (messages, params) => request('/chat', { messages, params }),
  /** @param {object} req RunRequest body; resolves to {id} */
  createRun: (req) => request('/runs', req),
  run: (id) => request(`/runs/${id}`),
  network: (id) => request(`/runs/${id}/network`),
  meta: (id, variant) => request(`/runs/${id}/${variant}/meta`),
  chunk: (id, variant, i) => request(`/runs/${id}/${variant}/chunks/${i}`),
};
