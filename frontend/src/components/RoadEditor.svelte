<script>
  // Popup for the clicked road edge: details and scenario edits
  // (close this direction, both directions, or specific lanes).
  import { app } from '../lib/store.svelte.js';

  const sel = $derived(app.selected);
  const reverse = $derived(app.network?.features.find((f) => f.properties.kind === 'edge'
    && f.properties.from === sel?.to && f.properties.to === sel?.from)?.properties ?? null);
  const isClosed = $derived(app.edits.close_edges.includes(sel?.uvk));
  let lanes = $state([]);

  /** Add or remove one directed edge from the pending closures. */
  function toggleEdge(uvk, close) {
    const list = app.edits.close_edges;
    const i = list.indexOf(uvk);
    if (close && i < 0) list.push(uvk);
    if (!close && i >= 0) list.splice(i, 1);
  }

  function closeBoth() {
    toggleEdge(sel.uvk, true);
    if (reverse) toggleEdge(reverse.uvk, true);
  }

  /** Close the chosen lanes on every edge that carries this road name. */
  function closeLanes() {
    if (!sel.name || !lanes.length) return;
    app.edits.close_lanes.push({ road: sel.name, lanes: [...lanes].sort((x, y) => x - y) });
    lanes = [];
  }
</script>

{#if sel}
  <div class="editor">
    <div class="head">
      <div>
        <div class="name">{sel.name || 'Unnamed road'}</div>
        <div class="sub">{sel.highway} · {sel.lanes} lane{sel.lanes > 1 ? 's' : ''} · {sel.vmax_kmh} km/h
          {#if sel.grade !== 'road'} · <span class="grade">{sel.grade}</span>{/if}
          {#if !sel.two_way} · one-way{/if}</div>
      </div>
      <button class="x" onclick={() => (app.selected = null)} aria-label="Close">×</button>
    </div>
    <div class="row">
      {#if isClosed}
        <button class="btn" onclick={() => toggleEdge(sel.uvk, false)}>Reopen this direction</button>
      {:else}
        <button class="btn danger" onclick={() => toggleEdge(sel.uvk, true)}>Close this direction</button>
        {#if reverse}<button class="btn danger" onclick={closeBoth}>Close both directions</button>{/if}
      {/if}
    </div>
    {#if sel.name && sel.lanes > 1}
      <div class="row lanes">
        <span class="label">Close lanes of {sel.name}:</span>
        {#each Array(sel.lanes) as _, k}
          <label class="lane"><input type="checkbox" value={k} bind:group={lanes} />{k === 0 ? '0 kerb' : k}</label>
        {/each}
        <button class="btn" disabled={!lanes.length} onclick={closeLanes}>Add</button>
      </div>
    {/if}
    <div class="hint">Edits apply to the next run (scenario vs baseline).</div>
  </div>
{/if}

<style>
  .editor { position: absolute; left: 14px; top: 92px; width: min(340px, calc(100% - 28px));
            background: var(--glass-strong); border: 1px solid var(--line-strong);
            border-radius: 12px; padding: 12px; display: grid; gap: 10px; backdrop-filter: blur(8px); }
  .head { display: flex; justify-content: space-between; gap: 8px; }
  .name { font-weight: 600; }
  .sub { font-size: 12px; color: var(--muted); margin-top: 2px; }
  .grade { color: #b392f0; }
  .x { background: none; border: 0; color: var(--muted); font-size: 18px; line-height: 1; }
  .row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  .lanes { font-size: 12px; }
  .label { color: var(--muted); width: 100%; }
  .lane { display: inline-flex; gap: 4px; align-items: center; }
  .hint { font-size: 11px; color: var(--muted); }
</style>
