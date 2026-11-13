<script>
  // App shell: header, left column (chat + parameters), centre (map +
  // playback), right column (metrics). Detects the backend on start-up and
  // falls back to the bundled demo replay when it is unreachable. Also wakes
  // the chat LLM so its cold start overlaps with the user typing.
  import { onMount } from 'svelte';
  import { app } from './lib/store.svelte.js';
  import { api, detectBackend } from './lib/api.js';
  import { openDemo, openRun, runFromHash } from './lib/runs.js';
  import Header from './components/Header.svelte';
  import ChatPanel from './components/ChatPanel.svelte';
  import ParamsPanel from './components/ParamsPanel.svelte';
  import MapView from './components/MapView.svelte';
  import Playback from './components/Playback.svelte';
  import MetricsPanel from './components/MetricsPanel.svelte';

  onMount(async () => {
    const live = await detectBackend();
    app.backend = live ? 'live' : 'demo';
    // Wake the chat LLM now: a cold GPU needs ~2 min, mostly hidden while the user types.
    if (live) api.warm().catch(() => {});
    const id = runFromHash();
    if (!live) await openDemo();
    else if (id) await openRun(id);
  });
</script>

<div class="shell">
  <Header />
  <main>
    <div class="col left">
      <ChatPanel />
      <ParamsPanel />
    </div>
    <div class="col centre">
      <MapView />
      <Playback />
    </div>
    <MetricsPanel />
  </main>
</div>

<style>
  .shell { height: 100vh; height: 100dvh; display: flex; flex-direction: column; }
  main { flex: 1; min-height: 0; display: grid; gap: 16px; padding: 16px;
         grid-template-columns: minmax(270px, 300px) minmax(0, 1fr) minmax(260px, 290px); }
  .col { display: flex; flex-direction: column; gap: 16px; min-height: 0; }
  .left { overflow-y: auto; }

  @media (max-width: 1100px) {
    main { grid-template-columns: minmax(260px, 300px) minmax(0, 1fr); }
    main > :global(.metrics) { grid-column: 1 / -1; max-height: none; }
  }
  @media (max-width: 760px) {
    .shell { height: auto; }
    main { grid-template-columns: 1fr; }
    .centre { order: -1; min-height: 70vh; }
  }
</style>
