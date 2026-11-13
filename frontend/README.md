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

## Deploy (Vercel)

```bash
cd frontend
vercel deploy                 # preview
vercel deploy --prod          # production
```

To connect a live backend, set `VITE_API_BASE_URL` (Production env) to the public FastAPI URL and redeploy. The backend already allows `https://*.vercel.app` in CORS.
