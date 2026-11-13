// Copy a finished run from a running backend into public/demo/ so the static
// build can replay it without a backend (the "demo replay" mode).
//
// Usage: node scripts/snapshot-demo.mjs <run-id> [api-base=http://localhost:8000]
// Output mirrors the API paths with a .json suffix:
//   public/demo/runs/demo.json, public/demo/runs/demo/network.json,
//   public/demo/runs/demo/<variant>/meta.json, .../<variant>/chunks/<i>.json

import { mkdir, rm, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const [runId, base = 'http://localhost:8000'] = process.argv.slice(2);
if (!runId) {
  console.error('usage: node scripts/snapshot-demo.mjs <run-id> [api-base]');
  process.exit(1);
}
const OUT = join(dirname(fileURLToPath(import.meta.url)), '..', 'public', 'demo', 'runs');
const HEADERS = { 'X-Session-Id': 'demo-snapshot-script' };

/** GET JSON from the API or throw. */
async function get(path) {
  const res = await fetch(`${base}/api${path}`, { headers: HEADERS });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}

/** Write compact JSON, creating folders. */
async function put(rel, obj) {
  const file = join(OUT, rel);
  await mkdir(dirname(file), { recursive: true });
  await writeFile(file, JSON.stringify(obj));
}

/** Copy one variant's meta and all chunks. */
async function copyVariant(variant) {
  const meta = await get(`/runs/${runId}/${variant}/meta`);
  await put(`demo/${variant}/meta.json`, meta);
  for (let i = 0; i < meta.n_chunks; i++) {
    await put(`demo/${variant}/chunks/${i}.json`, await get(`/runs/${runId}/${variant}/chunks/${i}`));
  }
  console.log(`${variant}: ${meta.n_chunks} chunks, ${meta.n_frames} frames`);
}

const run = await get(`/runs/${runId}`);
if (run.state !== 'done') throw new Error(`run ${runId} is ${run.state}, not done`);
await rm(OUT, { recursive: true, force: true });
await put('demo.json', { ...run, id: 'demo' });
await put('demo/network.json', await get(`/runs/${runId}/network`));
await copyVariant('baseline');
if (run.result.scenario) await copyVariant('scenario');
console.log(`demo written to ${OUT}`);
