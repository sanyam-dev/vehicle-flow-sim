<script>
  // Run parameters form + pending scenario edits; "Apply & restart" submits.
  import { app, editCount } from '../lib/store.svelte.js';
  import { buildRequest, startRun } from '../lib/runs.js';

  const busy = $derived(app.status === 'queued' || app.status === 'running');
  const nEdits = $derived(editCount());

  function apply(e) {
    e.preventDefault();
    const p = app.params;
    startRun(buildRequest({ ...p, duration_s: Number(p.duration_s), warmup_s: Number(p.warmup_s),
                            seed: Number(p.seed), dt: Number(p.dt) }));
  }

  function removeEdit(kind, i) {
    app.edits[kind].splice(i, 1);
  }
</script>

<section class="panel params">
  <div class="panel-head"><span>Hyperparameters</span></div>
  <form class="body" onsubmit={apply}>
    <label class="full">Place
      <input bind:value={app.params.place} required minlength="2" maxlength="160" />
    </label>
    <label>Traffic level
      <select bind:value={app.params.traffic}>
        <option value="less">Less · 500–700 veh/h</option>
        <option value="moderate">Moderate · 1000–1400</option>
        <option value="high">High · 2000+</option>
      </select>
    </label>
    <label>Start time
      <input type="time" bind:value={app.params.time} required />
    </label>
    <label>Duration (s)
      <input type="number" min="120" max="3600" step="60" bind:value={app.params.duration_s} />
    </label>
    <label>Warm-up (s)
      <input type="number" min="0" max="900" step="30" bind:value={app.params.warmup_s} />
    </label>
    <label>Time step (s)
      <input type="number" min="0.1" max="1" step="0.1" bind:value={app.params.dt} />
    </label>
    <label>Random seed
      <input type="number" min="0" step="1" bind:value={app.params.seed} />
    </label>

    {#if nEdits}
      <div class="full edits">
        <div class="edits-head">Scenario edits ({nEdits})</div>
        {#each app.edits.close_edges as uvk, i}
          <div class="edit"><span>Close edge <code>{uvk}</code></span>
            <button type="button" onclick={() => removeEdit('close_edges', i)} aria-label="Remove">×</button></div>
        {/each}
        {#each app.edits.close_roads as road, i}
          <div class="edit"><span>Close road “{road}”</span>
            <button type="button" onclick={() => removeEdit('close_roads', i)} aria-label="Remove">×</button></div>
        {/each}
        {#each app.edits.close_lanes as c, i}
          <div class="edit"><span>Close lane{c.lanes.length > 1 ? 's' : ''} {c.lanes.join(', ') || 'all'} of “{c.road}”</span>
            <button type="button" onclick={() => removeEdit('close_lanes', i)} aria-label="Remove">×</button></div>
        {/each}
      </div>
    {:else}
      <p class="full hint">Click a road on the map to close it, a direction or lanes. With no edits the run is baseline only.</p>
    {/if}

    {#if app.error}<p class="full err">{app.error}</p>{/if}
    <button class="btn full" type="submit" disabled={busy || app.backend !== 'live'}>
      {busy ? `Simulating… ${Math.round(app.progress * 100)}%` : 'Apply & restart'}
    </button>
  </form>
</section>

<style>
  .body { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; padding: 4px 16px 16px; }
  label { display: grid; gap: 6px; font-size: 12.5px; color: var(--muted); }
  .full { grid-column: 1 / -1; }
  .hint { margin: 0; font-size: 12px; color: var(--muted); }
  .err { margin: 0; font-size: 12px; color: var(--jam); }
  .edits { display: grid; gap: 6px; font-size: 12.5px; }
  .edits-head { color: var(--muted); }
  .edit { display: flex; justify-content: space-between; gap: 8px; background: var(--panel-2);
          border-radius: 6px; padding: 6px 8px; }
  .edit button { background: none; border: 0; color: var(--muted); }
  code { font-family: var(--mono); font-size: 11px; }
</style>
