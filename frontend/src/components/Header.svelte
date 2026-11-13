<script>
  // Top bar: brand, scenario name, run status, sim clock, variant switch,
  // export and new-run actions.
  import { app, DEFAULT_PARAMS } from '../lib/store.svelte.js';
  import { player } from '../lib/player.svelte.js';
  import { attachVariant } from '../lib/runs.js';
  import { clockAt, hms } from '../lib/format.js';

  const hasScenario = $derived(!!app.result?.scenario);
  const title = $derived(app.request
    ? `${app.request.place.split(',')[0]} · ${app.request.traffic ?? 'auto'} · ${app.request.time}`
    : 'Untitled scenario');
  const status = $derived.by(() => {
    if (app.backend === 'checking') return ['Connecting', 'idle'];
    if (app.status === 'queued') return ['Queued', 'busy'];
    if (app.status === 'running') return [`Simulating ${Math.round(app.progress * 100)}%`, 'busy'];
    if (app.status === 'error') return ['Error', 'error'];
    if (app.status === 'done') return [player.playing ? 'Running' : 'Paused', player.playing ? 'ok' : 'idle'];
    return ['Idle', 'idle'];
  });

  /** Download the run's request + results as JSON. */
  function exportRun() {
    const blob = new Blob([JSON.stringify({ id: app.runId, request: app.request, result: app.result }, null, 1)],
                          { type: 'application/json' });
    const a = Object.assign(document.createElement('a'),
                            { href: URL.createObjectURL(blob), download: `vfsim-run-${app.runId}.json` });
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  /** Start over: clear chat, edits and the form. */
  function newRun() {
    player.pause();
    app.chat = [{ role: 'assistant', content: 'What should we simulate?' }];
    app.proposal = null;
    app.edits = { close_edges: [], close_roads: [], close_lanes: [] };
    app.params = { ...DEFAULT_PARAMS };
    app.selected = null;
    document.querySelector('.chat-input')?.focus();
  }
</script>

<header>
  <div class="brand">
    <svg viewBox="0 0 32 32" width="28" height="28" aria-hidden="true"><rect x="2" y="2" width="28" height="28" rx="7" fill="none" stroke="var(--accent)" stroke-width="2.5"/><path d="M10 23l6-14 6 14M12.5 18h7" fill="none" stroke="var(--accent)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
    <span class="name">Traffic Sim Studio</span>
    <span class="sep">/</span>
    <span class="scenario" title={title}>{title}</span>
    <span class="pill {status[1]}"><i></i>{status[0]}</span>
    {#if app.backend === 'demo'}<span class="pill demo">Demo replay</span>{/if}
  </div>
  <div class="right">
    {#if hasScenario}
      <div class="seg" role="tablist" aria-label="Replay variant">
        {#each ['baseline', 'scenario'] as v}
          <button class:on={app.variant === v} onclick={() => attachVariant(v)}>{v}</button>
        {/each}
      </div>
    {/if}
    <span class="clock" title="Elapsed · time of day">t = {hms(player.t)}
      {#if app.request}<span class="tod">{clockAt(app.request.time, player.t)}</span>{/if}</span>
    <button class="btn" disabled={!app.result} onclick={exportRun}>Export run</button>
    <button class="btn primary" onclick={newRun}>New run</button>
  </div>
</header>

<style>
  header { display: flex; align-items: center; justify-content: space-between; gap: 16px;
           padding: 12px 20px; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
  .brand, .right { display: flex; align-items: center; gap: 12px; min-width: 0; flex-wrap: wrap; }
  .name { font-weight: 600; font-size: 16px; }
  .sep { color: var(--muted); }
  .scenario { color: var(--text-2); max-width: 32ch; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .pill { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; padding: 3px 9px;
          border-radius: 999px; background: var(--panel-2); color: var(--muted); }
  .pill i { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
  .pill.ok { background: rgba(46, 196, 165, 0.14); color: var(--free); }
  .pill.busy { background: rgba(245, 165, 36, 0.14); color: var(--accent); }
  .pill.error { background: rgba(239, 68, 68, 0.14); color: var(--jam); }
  .pill.demo { background: rgba(122, 167, 255, 0.14); color: #9db9ff; }
  .clock { font-family: var(--mono); font-size: 14px; color: var(--text-2); }
  .tod { margin-left: 8px; color: var(--muted); }
  .seg { display: flex; background: var(--panel-2); border-radius: 8px; padding: 3px; }
  .seg button { background: none; border: 0; color: var(--muted); padding: 6px 12px; border-radius: 6px;
                text-transform: capitalize; font-size: 13px; }
  .seg button.on { background: var(--line); color: var(--text); }
</style>
