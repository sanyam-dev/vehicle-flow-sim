<script>
  // Popup for the clicked junction: its id and control, and the choice to make
  // it a fixed-time traffic signal (with its own stop duration) or a circle.
  import { app } from '../lib/store.svelte.js';

  const NAMES = { signal: 'Traffic signal', roundabout: 'Circle', priority: 'Give way (no control)' };
  const sel = $derived(app.selectedNode);
  const pending = $derived(app.edits.junction_edits.find((j) => j.node === sel?.osm) ?? null);
  let control = $state('signal');
  let stop = $state(45);

  // Start from the pending edit, else from what the junction is now.
  $effect(() => {
    control = pending?.control ?? (sel?.kind === 'roundabout' ? 'circle' : 'signal');
    stop = pending?.stop_s ?? sel?.stop_s ?? app.params.signal_stop_s;
  });

  const stopOk = $derived(Number(stop) >= 10 && Number(stop) <= 180);

  /** Add or replace this junction's pending edit. */
  function apply() {
    remove();
    app.edits.junction_edits.push({ node: sel.osm, label: sel.label, control,
                                    stop_s: control === 'signal' ? Number(stop) : null });
  }

  function remove() {
    const i = app.edits.junction_edits.findIndex((j) => j.node === sel.osm);
    if (i >= 0) app.edits.junction_edits.splice(i, 1);
  }
</script>

{#if sel}
  <div class="editor" role="dialog" aria-label="Junction editor">
    <div class="head">
      <div>
        <div class="name">Junction {sel.label}</div>
        <div class="sub">Now: {NAMES[sel.kind] ?? sel.kind}{sel.stop_s ? ` · ${sel.stop_s} s red` : ''}</div>
      </div>
      <button class="x" onclick={() => (app.selectedNode = null)} aria-label="Close">×</button>
    </div>
    <div class="seg" role="radiogroup" aria-label="Junction control">
      <button role="radio" aria-checked={control === 'signal'} class:on={control === 'signal'}
              onclick={() => (control = 'signal')}>Traffic signal</button>
      <button role="radio" aria-checked={control === 'circle'} class:on={control === 'circle'}
              onclick={() => (control = 'circle')}>Circle</button>
    </div>
    {#if control === 'signal'}
      <label class="row">Stop (red) duration, s
        <input type="number" min="10" max="180" step="5" bind:value={stop} />
      </label>
      <div class="hint">Fixed-time signal: every approach waits this long on red and all get the same
        green. Green is never under 10 s, so 4 approaches wait at least 47 s.</div>
    {:else}
      <div class="hint">Vehicles go round an island at 20 km/h in single file; entering traffic gives way
        to traffic already circulating.</div>
    {/if}
    <div class="row">
      <button class="btn primary" disabled={control === 'signal' && !stopOk} onclick={apply}>
        {pending ? 'Update edit' : 'Add to scenario'}</button>
      {#if pending}<button class="btn" onclick={remove}>Remove edit</button>{/if}
    </div>
    <div class="hint">Edits apply to the next run (scenario vs baseline).</div>
  </div>
{/if}

<style>
  .editor { position: absolute; left: 14px; top: 92px; width: min(330px, calc(100% - 28px));
            background: var(--glass-strong); border: 1px solid var(--line-strong);
            border-radius: 12px; padding: 12px; display: grid; gap: 10px; backdrop-filter: blur(8px); }
  .head { display: flex; justify-content: space-between; gap: 8px; }
  .name { font-weight: 600; }
  .sub { font-size: 12px; color: var(--muted); margin-top: 2px; }
  .x { background: none; border: 0; color: var(--muted); font-size: 18px; line-height: 1; }
  .seg { display: flex; background: var(--panel-2); border-radius: 8px; padding: 3px; }
  .seg button { flex: 1; background: none; border: 0; color: var(--muted); padding: 6px 8px;
                border-radius: 6px; font-size: 13px; }
  .seg button.on { background: var(--line-strong); color: var(--text); }
  .row { display: flex; gap: 8px; align-items: center; font-size: 13px; color: var(--text-2); }
  .row input { width: 84px; margin-left: auto; padding: 5px 8px; }
  .hint { font-size: 12px; color: var(--muted); }
</style>
