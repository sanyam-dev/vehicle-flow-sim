<script>
  // Simulation display: Leaflet map locked to the simulated area, with the road
  // network, vehicles, satellite toggle, the pencil (map editor), legend and
  // the road / junction editor popups.
  import { onMount } from 'svelte';
  import L from 'leaflet';
  import { app } from '../lib/store.svelte.js';
  import { player } from '../lib/player.svelte.js';
  import { NetworkLayer } from '../lib/maplayers.js';
  import { VehicleLayer } from '../lib/vehiclelayer.js';
  import { MapSync } from '../lib/mapsync.js';
  import { unappliedEdits } from '../lib/runs.js';
  import { TOOL_ICONS } from '../lib/icons.js';
  import Legend from './Legend.svelte';
  import RoadEditor from './RoadEditor.svelte';
  import JunctionEditor from './JunctionEditor.svelte';
  import EditPanel from './EditPanel.svelte';
  import PointChooser from './PointChooser.svelte';

  const SAT_URL = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';

  let container;
  let map = $state(null);
  let ready = $state(false);
  let netLayer = null;
  let vehicles = null;
  let satLayer = null;
  let sync = null;
  let selectedId = null;
  let hover = $state(null); // snapped road point under the cursor (edit mode)
  let view = $state(0); // bumped on every pan / zoom
  let litId = null; // edge highlighted for the chooser

  // Option the stacked-road chooser currently points at, and where it sits on screen.
  const chosen = $derived(app.choice ? app.choice.options[app.choice.index] : null);
  const chooserAt = $derived.by(() => {
    view; // re-place after pan / zoom
    return chosen && map ? map.latLngToContainerPoint([chosen.lat, chosen.lon]) : null;
  });

  const junctionCount = $derived(
    app.network?.features.filter((f) => ['signal', 'priority', 'roundabout'].includes(f.properties.kind)).length ?? 0);
  const signalCount = $derived(
    app.network?.features.filter((f) => f.properties.kind === 'signal').length ?? 0);

  // Elapsed time of the current run, ticking once a second while it is busy.
  let now = $state(Date.now());
  const busy = $derived(app.status === 'queued' || app.status === 'running');
  $effect(() => {
    if (!busy) return;
    const t = setInterval(() => (now = Date.now()), 1000);
    return () => clearInterval(t);
  });
  const elapsed = $derived.by(() => {
    if (!app.startedAt) return '';
    const s = Math.max(0, Math.floor((now - app.startedAt) / 1000));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  });

  /**
   * Edit mode: snap the click to a road. Where roads are stacked the user
   * confirms one in the chooser (ground road preselected); else it is taken.
   */
  function pick(latlng) {
    const options = netLayer.roadsAt(latlng);
    if (!options.length) return;
    if (options.length === 1) place(options[0]);
    else app.choice = { options, index: 0 };
  }

  /** Use a snapped point as point A, then B. */
  function place(pt) {
    app.choice = null;
    if (!app.draft.a || app.draft.b) app.draft = { a: pt, b: null };
    else app.draft = { a: app.draft.a, b: pt };
  }

  /** Keys while the stacked-road chooser is open: arrows choose, Enter confirms. */
  function chooserKey(e) {
    const c = app.choice;
    if (!c) return false;
    if (e.key === 'Escape') app.choice = null;
    else if (e.key === 'Enter') place(c.options[c.index]);
    else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      const step = e.key === 'ArrowDown' ? 1 : -1;
      app.choice = { ...c, index: (c.index + step + c.options.length) % c.options.length };
    } else return false;
    e.preventDefault();
    return true;
  }

  /** A road was clicked: pick a point in edit mode, else open the road editor. */
  function onEdge(p, latlng) {
    if (app.editMode) return pick(latlng);
    app.selectedNode = null;
    app.selected = { ...p };
  }

  /** A junction was clicked: open the junction editor (not while drawing). */
  function onNode(p) {
    if (app.editMode) return;
    app.selected = null;
    app.selectedNode = { ...p };
  }

  /** Create the map, layers and click handlers. */
  function initMap() {
    // zoomAnimation off: roads (canvas) and vehicles (own canvas) then always move together.
    map = L.map(container, { zoomControl: false, zoomAnimation: false, minZoom: 12, maxZoom: 20,
                             zoomSnap: 0.25, maxBoundsViscosity: 1 }).setView([12.916, 77.624], 15);
    map.attributionControl.setPrefix('<a href="https://leafletjs.com">Leaflet</a>');
    netLayer = new NetworkLayer(map, { onEdge, onNode });
    vehicles = new VehicleLayer().addTo(map);
    map.on('click', (e) => {
      if (app.editMode) pick(e.latlng);
      else Object.assign(app, { selected: null, selectedNode: null });
    });
    map.on('mousemove', (e) => {
      hover = app.editMode && !app.choice ? (netLayer.roadsAt(e.latlng)[0] ?? null) : null;
    });
    map.on('mouseout', () => (hover = null));
    map.on('move zoom', () => view++); // keeps the chooser next to its point
    sync = new MapSync(netLayer, vehicles);
    player.renderer = (g, s, t) => sync.render(g, s, t);
    const resize = new ResizeObserver(() => { map.invalidateSize({ pan: false }); netLayer.relock(); });
    resize.observe(container);
    const esc = (e) => {
      if (chooserKey(e)) return;
      if (e.key === 'Escape') Object.assign(app, { editMode: false, draft: { a: null, b: null } });
    };
    window.addEventListener('keydown', esc);
    ready = true;
    return () => { resize.disconnect(); window.removeEventListener('keydown', esc); };
  }

  onMount(() => {
    const stop = initMap();
    return () => { stop(); player.renderer = null; map?.remove(); };
  });

  // New network: draw it and lock the view (and the satellite tiles) to its area.
  $effect(() => {
    const net = app.network;
    if (!ready) return;
    satLayer?.remove();
    satLayer = null;
    if (!net) {
      netLayer.clear();
      vehicles.setData({ features: [] });
      return;
    }
    const [w, s, e, n] = net.meta.area_bbox ?? net.meta.bbox;
    satLayer = L.tileLayer(SAT_URL, { maxZoom: 20, maxNativeZoom: 19, className: 'sat-tiles',
                                      bounds: [[s, w], [n, e]],
                                      attribution: 'Imagery © Esri, Maxar, Earthstar Geographics' });
    netLayer.setNetwork(net);
    sync.setNetwork(net);
    netLayer.lock([w, s, e, n]);
  });

  // No area yet: nothing to look at, so the empty map cannot be dragged around.
  $effect(() => {
    if (!ready) return;
    for (const h of [map.dragging, map.scrollWheelZoom, map.doubleClickZoom, map.touchZoom]) {
      if (app.network) h.enable(); else h.disable();
    }
  });

  $effect(() => {
    const on = app.satellite && !!app.network;
    if (!ready || !satLayer) return;
    if (on) satLayer.addTo(map); else satLayer.remove();
  });

  // Which run is shown: roads, lanes, junction controls and signal timings.
  $effect(() => {
    const variant = app.variant;
    const signals = app.result?.signals?.[variant] ?? null;
    if (!ready || !app.network) return;
    netLayer.setVariant(app.result?.scenario ? variant : 'scenario');
    netLayer.setSignals(signals);
  });

  $effect(() => { if (ready) netLayer.setHeat(app.heat); });

  // Closed roads: edits not yet run + (scenario view) the run's own closures.
  // Edits the loaded run already applied are drawn from its network, per variant.
  $effect(() => {
    const pending = unappliedEdits().close_edges;
    const showRun = app.variant === 'scenario';
    if (ready && app.network) sync.applyClosures(pending, showRun);
  });

  // Map edits not yet run, the edit being drawn, and edited junctions.
  $effect(() => {
    const todo = unappliedEdits();
    const edits = todo.map_edits;
    const draft = { ...app.draft };
    const ids = new Set(todo.junction_edits.map((j) => String(j.node)));
    const cursor = chosen ?? (app.editMode && !(draft.a && draft.b) ? hover : null);
    if (!ready || !app.network) return;
    netLayer.setPreviews(edits, draft, cursor);
    netLayer.setNodeEdits(ids);
  });

  // Chooser: highlight the road its current option snaps to.
  $effect(() => {
    const id = chosen?.id ?? null;
    if (!ready) return;
    sync.select(id, litId);
    litId = id;
  });

  // Selection highlight.
  $effect(() => {
    const id = app.selected?.edge ?? null;
    if (!ready) return;
    sync.select(id, selectedId);
    selectedId = id;
  });

  // A junction picked elsewhere (metrics table): bring it into view.
  $effect(() => {
    const node = app.focusNode;
    if (ready && node !== null && node !== undefined) {
      netLayer.focusNode(node);
      app.focusNode = null;
    }
  });

  function toggleEdit() {
    Object.assign(app, { editMode: !app.editMode, draft: { a: null, b: null }, choice: null,
                         selected: null, selectedNode: null });
  }
</script>

<div class="map-wrap" class:drawing={app.editMode}>
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
      {#if busy}
        <div class="spinner"></div>
        <div>{app.stage || 'Queued'}…</div>
        {#if elapsed}<div class="elapsed">{elapsed}</div>{/if}
      {:else}
        <div>Describe a place in the chat, or press <b>Apply &amp; restart</b>.</div>
      {/if}
    </div>
  {/if}

  <div class="tools">
    <button class="tool" title="Zoom in" aria-label="Zoom in" onclick={() => map?.zoomIn()}>{@html TOOL_ICONS.plus}</button>
    <button class="tool" title="Zoom out (limited to the simulated area)" aria-label="Zoom out"
            onclick={() => map?.zoomOut()}>{@html TOOL_ICONS.minus}</button>
    <button class="tool" class:on={app.satellite} title="Satellite reference" aria-label="Satellite reference"
            aria-pressed={app.satellite} disabled={!app.network}
            onclick={() => (app.satellite = !app.satellite)}>{@html TOOL_ICONS.satellite}</button>
    <button class="tool" class:on={app.editMode} aria-pressed={app.editMode} disabled={!app.network}
            title="Edit the map: add a flyover, underpass or lane between two points"
            aria-label="Edit the map" onclick={toggleEdit}>{@html TOOL_ICONS.pencil}</button>
    <button class="tool" class:on={app.heat} aria-pressed={app.heat} disabled={!app.network}
            title="Colour every road by speed" aria-label="Speed heatmap"
            onclick={() => (app.heat = !app.heat)}>{@html TOOL_ICONS.heat}</button>
  </div>

  {#if app.editMode}
    <EditPanel />
    {#if app.choice && chooserAt}
      <PointChooser options={app.choice.options} index={app.choice.index} x={chooserAt.x} y={chooserAt.y}
                    onpick={(i) => (app.choice = { ...app.choice, index: i })}
                    onconfirm={() => place(chosen)} oncancel={() => (app.choice = null)} />
    {/if}
  {:else if app.selected}
    <RoadEditor />
  {:else if app.selectedNode}
    <JunctionEditor />
  {/if}

  <Legend />
</div>

<style>
  .map-wrap { position: relative; flex: 1; min-height: 0; border: 1px solid var(--line);
              border-radius: var(--radius); overflow: hidden; background: var(--map-bg); }
  .map { position: absolute; inset: 0; z-index: 0; } /* own stacking context: Leaflet panes stay under the overlays */
  .map-inner { width: 100%; height: 100%; }
  .drawing :global(.leaflet-container) { cursor: crosshair; }
  .card { position: absolute; background: var(--glass); border: 1px solid var(--line);
          border-radius: 10px; padding: 10px 12px; backdrop-filter: blur(6px); }
  .net-card { top: 14px; left: 14px; max-width: 60%; }
  .eyebrow { font-family: var(--mono); font-size: 11px; letter-spacing: 0.12em;
             text-transform: uppercase; color: var(--muted); }
  .net-title { font-weight: 600; margin-top: 3px; }
  .net-sub { font-size: 12px; color: var(--muted); margin-top: 2px; }
  .tools { position: absolute; top: 14px; right: 14px; display: flex; flex-direction: column; gap: 6px; }
  .tool { width: 36px; height: 36px; border-radius: 9px; background: var(--glass); display: grid;
          place-items: center; border: 1px solid var(--line); color: var(--text-2);
          backdrop-filter: blur(6px); }
  .tool:hover:not(:disabled) { border-color: var(--line-strong); color: var(--text); }
  .tool:disabled { opacity: 0.4; }
  .tool.on { background: var(--accent); color: #17130a; border-color: var(--accent); }
  .empty { position: absolute; inset: 0; display: grid; place-content: center; justify-items: center;
           gap: 12px; color: var(--muted); text-align: center; padding: 24px; pointer-events: none; }
  .spinner { width: 28px; height: 28px; border-radius: 50%; border: 3px solid var(--line);
             border-top-color: var(--accent); animation: spin 0.9s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .elapsed { font-variant-numeric: tabular-nums; font-size: 12px; }
</style>
