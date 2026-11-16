<script>
  // Map editor (pencil): pick two points on roads, then add a flyover, an
  // underpass or an extra lane between them. The edit joins the scenario of
  // the next run.
  import { app } from '../lib/store.svelte.js';
  import { metresBetween } from '../lib/mapgeom.js';

  const KINDS = [['flyover', 'Flyover'], ['underpass', 'Underpass'], ['add_lane', 'Extra lane']];
  const MIN_M = 50;
  let kind = $state('flyover');
  let lanes = $state(2);
  let both = $state(true);

  const a = $derived(app.draft.a);
  const b = $derived(app.draft.b);
  const dist = $derived(a && b ? metresBetween(a, b) : 0);
  const tooShort = $derived(!!b && kind !== 'add_lane' && dist < MIN_M);
  const full = $derived(app.edits.map_edits.length >= 10);

  /** Add the drawn edit to the pending scenario edits and start a fresh one. */
  function add() {
    app.edits.map_edits.push({ kind, lanes: Number(lanes), both_directions: both,
                               a: { ...a }, b: { ...b } });
    app.draft = { a: null, b: null };
  }

  function close() {
    Object.assign(app, { editMode: false, draft: { a: null, b: null }, choice: null });
  }

  /** "Name" for a ground road, "Name (flyover)" / "(underpass)" for the other levels. */
  const pointLabel = (p) => p.name + ({ bridge: ' (flyover)', underpass: ' (underpass)' }[p.grade] ?? '');
</script>

<div class="editor" role="dialog" aria-label="Map editor">
  <div class="head">
    <div class="name">Edit the map</div>
    <button class="x" onclick={close} aria-label="Close map editor">×</button>
  </div>
  <ol class="steps">
    <li class:done={!!a} class:now={!a}>
      {a ? `Point A · ${pointLabel(a)}` : 'Click the first point on a road'}</li>
    <li class:done={!!b} class:now={!!a && !b}>
      {b ? `Point B · ${pointLabel(b)}` : 'Click the second point on a road'}</li>
  </ol>
  {#if a && b}
    <div class="seg" role="radiogroup" aria-label="What to add">
      {#each KINDS as [value, label]}
        <button role="radio" aria-checked={kind === value} class:on={kind === value}
                onclick={() => (kind = value)}>{label}</button>
      {/each}
    </div>
    {#if kind === 'add_lane'}
      <label class="check"><input type="checkbox" bind:checked={both} /> Both directions</label>
      <div class="hint">One lane is added to every road on the shortest route from A to B.</div>
    {:else}
      <label class="row">Lanes each way
        <select bind:value={lanes}>{#each [1, 2, 3, 4] as n}<option value={n}>{n}</option>{/each}</select>
      </label>
      <div class="hint">A straight {kind}, {Math.round(dist)} m long. It joins the roads only at A and B and
        passes {kind === 'flyover' ? 'over' : 'under'} everything between.</div>
    {/if}
    {#if tooShort}<div class="err">The points are {Math.round(dist)} m apart; a {kind} needs at least {MIN_M} m.</div>{/if}
    {#if full}<div class="err">A scenario holds at most 10 map edits.</div>{/if}
    <div class="row">
      <button class="btn primary" disabled={tooShort || full} onclick={add}>Add to scenario</button>
      <button class="btn" onclick={() => Object.assign(app, { draft: { a: null, b: null }, choice: null })}>Pick again</button>
    </div>
  {:else}
    <div class="hint">Clicks snap to the nearest road; where a flyover or underpass is stacked on it you choose which. Press Esc to stop.</div>
  {/if}
  {#if app.edits.map_edits.length}
    <div class="hint">{app.edits.map_edits.length} map edit{app.edits.map_edits.length > 1 ? 's' : ''} pending ·
      press <b>Apply &amp; restart</b> to simulate.</div>
  {/if}
</div>

<style>
  .editor { position: absolute; left: 14px; top: 92px; width: min(330px, calc(100% - 28px));
            background: var(--glass-strong); border: 1px solid var(--line-strong);
            border-radius: 12px; padding: 12px; display: grid; gap: 10px; backdrop-filter: blur(8px); }
  .head { display: flex; justify-content: space-between; align-items: center; }
  .name { font-weight: 600; }
  .x { background: none; border: 0; color: var(--muted); font-size: 18px; line-height: 1; }
  .steps { margin: 0; padding-left: 20px; display: grid; gap: 4px; font-size: 13px; color: var(--muted); }
  .steps .now { color: var(--text); }
  .steps .done { color: var(--free); }
  .seg { display: flex; background: var(--panel-2); border-radius: 8px; padding: 3px; }
  .seg button { flex: 1; background: none; border: 0; color: var(--muted); padding: 6px 8px;
                border-radius: 6px; font-size: 13px; }
  .seg button.on { background: var(--line-strong); color: var(--text); }
  .row { display: flex; gap: 8px; align-items: center; font-size: 13px; color: var(--text-2); }
  .row select { width: 70px; margin-left: auto; padding: 5px 8px; }
  .check { display: flex; gap: 8px; align-items: center; font-size: 13px; color: var(--text-2); }
  .hint { font-size: 12px; color: var(--muted); }
  .err { font-size: 12px; color: var(--jam); }
</style>
