# Traffic Sim Studio: frontend (Svelte 5 + Vite + Leaflet + uPlot)

## Layout (from `ui-frame.png`)

| Area | Contents |
|---|---|
| Header | Run status, baseline/scenario switch, sim clock, Export run, New run |
| Left | Chat with the task-understanding agent; run parameters and pending scenario edits |
| Centre | Map with roads coloured by speed, vehicle icons, junction queues, satellite toggle and legend. Click a road to close a direction, both directions or lanes. Playback bar below |
| Right | Live metrics with sparklines and a congestion meter. The Results tab has the KPI comparison, uPlot charts and the slowest junctions |

## Run locally

```bash
cd backend && uv run uvicorn app.main:app --port 8000   # API
cd frontend && npm install && npm run dev                # http://localhost:5173, proxies /api
```

`VITE_DEV_API` changes the proxy target. Every request sends `X-Session-Id`, a UUID kept in localStorage, so each browser gets its own worker process.

## Demo mode

At start-up the app probes `${VITE_API_BASE_URL}/api/health`. If nothing answers, it replays the recorded run in `public/demo/` (read-only: chat and Apply are disabled).

Refresh the demo from a finished run on a running backend:

```bash
node scripts/snapshot-demo.mjs <run-id> http://localhost:8000
```

## Deploy (Google Cloud Run)

The frontend is not deployed on its own. The root `Dockerfile` builds it and
FastAPI serves it next to the API from one Cloud Run service (same origin, so
`VITE_API_BASE_URL` stays empty). See `deploy/gcp/deploy.sh`.

The chat agent's LLM is a second, private Cloud Run service: Ollama with
`qwen3:8b` on one L4 GPU in `asia-southeast1`, scaled to zero when idle
(`deploy/gcp/deploy-ollama.sh`, run before `deploy.sh`). Only the app's
service account may call it. The first chat after an idle spell waits for a
cold start; if the LLM is unreachable the chat falls back to the keyword parser.
