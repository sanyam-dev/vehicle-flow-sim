<script>
  // Simulation display: Leaflet map with the road network, vehicles,
  // satellite toggle, zoom controls, legend and the road editor popup.
  import { onMount } from 'svelte';
  import L from 'leaflet';
  import { app } from '../lib/store.svelte.js';
  import { player } from '../lib/player.svelte.js';
  import { NetworkLayer } from '../lib/maplayers.js';
  import { VehicleLayer } from '../lib/vehiclelayer.js';
  import { MapSync } from '../lib/mapsync.js';
  import Legend from './Legend.svelte';
  import RoadEditor from './RoadEditor.svelte';

  const SAT_URL = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';

  let container;
  let map = $state(null);
  let ready = $state(false);
  let netLayer = null;
  let vehicles = null;
  let satLayer = null;
  let sync = null;
  let selectedId = null;

  const junctionCount = $derived(
    app.network?.features.filter((f) => ['signal', 'priority'].includes(f.properties.kind)).length ?? 0);
  const signalCount = $derived(
    app.network?.features.filter((f) => f.properties.kind === 'signal').length ?? 0);

  /** Create the map, layers and click handlers. */
  function initMap() {
    // zoomAnimation off: roads (canvas) and vehicles (own canvas) then always move together.
    map = L.map(container, { zoomControl: false, zoomAnimation: false, minZoom: 12, maxZoom: 20,
                             zoomSnap: 0.5 }).setView([12.916, 77.624], 15);
    map.attributionControl.setPrefix('<a href="https://leafletjs.com">Leaflet</a>');
    satLayer = L.tileLayer(SAT_URL, { maxZoom: 20, maxNativeZoom: 19, className: 'sat-tiles',
                                      attribution: 'Imagery © Esri, Maxar, Earthstar Geographics' });
    netLayer = new NetworkLayer(map, (p) => (app.selected = p ? { ...p } : null));
    vehicles = new VehicleLayer().addTo(map);
    map.on('click', () => (app.selected = null));
    sync = new MapSync(netLayer, vehicles);
    player.renderer = (g, s, t) => sync.render(g, s, t);
    const resize = new ResizeObserver(() => map.invalidateSize({ pan: false }));
    resize.observe(container);
    ready = true;
    return () => resize.disconnect();
  }

  onMount(() => {
    const stop = initMap();
    return () => { stop(); player.renderer = null; map?.remove(); };
  });

  // New network: draw it (with the grid) and fit the view.
  $effect(() => {
    const net = app.network;
    if (!ready) return;
    if (!net) {
      netLayer.clear();
      vehicles.setData({ features: [] });
      return;
    }
    netLayer.setNetwork(net);
    sync.setNetwork(net);
    netLayer.fit(net.meta.bbox);
  });

  // Closed roads: pending edits + (scenario view) the run's own closures.
  $effect(() => {
    const pending = app.edits.close_edges.slice();
    const showRun = app.variant === 'scenario';
    if (ready && app.network) sync.applyClosures(pending, showRun);
  });

  // Selection highlight.
  $effect(() => {
    const id = app.selected?.edge ?? null;
    if (!ready) return;
    sync.select(id, selectedId);
    selectedId = id;
  });

  $effect(() => {
    if (!ready) return;
    if (app.satellite) satLayer.addTo(map);
    else satLayer.remove();
  });
</script>

<div class="map-wrap">
  <!-- Leaflet owns the inner div. If the stylesheet is not applied yet when the
       map is created (Safari), Leaflet writes an inline position: relative on its
       container; sizing with width/height 100% inside the positioned outer box
       keeps the map full size either way. -->
  <div class="map"><div class="map-inner" bind:this={container}></div></div>

  {#if app.network}
    <div class="card net-card">
      <div class="eyebrow">Road network</div>
      <div class="net-title">{app.network.meta.area ?? 'Area'}</div>
      <div class="net-sub">{app.network.meta.edges} road links · {junctionCount} junctions · {signalCount} signals</div>
    </div>
  {:else}
    <div class="empty">
      {#if app.status === 'queued' || app.status === 'running'}
        <div class="spinner"></div>
        <div>{app.stage || 'Queued'}…</div>
      {:else}
        <div>Describe a place in the chat, or press <b>Apply &amp; restart</b>.</div>
      {/if}
    </div>
  {/if}

  <div class="tools">
    <button class="tool" title="Zoom in" onclick={() => map?.zoomIn()}>+</button>
    <button class="tool" title="Zoom out" onclick={() => map?.zoomOut()}>−</button>
    <button class="tool sat" class:on={app.satellite} title="Satellite reference"
            onclick={() => (app.satellite = !app.satellite)}>SAT</button>
  </div>

  {#if app.selected}
    <RoadEditor />
  {/if}

  <Legend />
</div>

<style>
  .map-wrap { position: relative; flex: 1; min-height: 0; border: 1px solid var(--line);
              border-radius: var(--radius); overflow: hidden; background: var(--map-bg); }
  .map { position: absolute; inset: 0; z-index: 0; } /* own stacking context: Leaflet panes stay under the overlays */
  .map-inner { width: 100%; height: 100%; }
  .card { position: absolute; background: rgba(17, 23, 32, 0.92); border: 1px solid var(--line);
          border-radius: 8px; padding: 10px 12px; }
  .net-card { top: 14px; left: 14px; max-width: 60%; }
  .eyebrow { font-family: var(--mono); font-size: 11px; letter-spacing: 0.12em;
             text-transform: uppercase; color: var(--muted); }
  .net-title { font-weight: 600; margin-top: 3px; }
  .net-sub { font-size: 12px; color: var(--muted); margin-top: 2px; }
  .tools { position: absolute; top: 14px; right: 14px; display: flex; flex-direction: column; gap: 8px; }
  .tool { width: 38px; height: 38px; border-radius: 8px; background: rgba(17, 23, 32, 0.92);
          border: 1px solid var(--line); color: var(--text); font-size: 18px; }
  .tool:hover { border-color: var(--line-strong); }
  .sat { font-family: var(--mono); font-size: 10px; letter-spacing: 0.05em; }
  .sat.on { background: var(--accent); color: #111; border-color: var(--accent); }
  .empty { position: absolute; inset: 0; display: grid; place-content: center; justify-items: center;
           gap: 12px; color: var(--muted); text-align: center; padding: 24px; pointer-events: none; }
  .spinner { width: 28px; height: 28px; border-radius: 50%; border: 3px solid var(--line);
             border-top-color: var(--accent); animation: spin 0.9s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
