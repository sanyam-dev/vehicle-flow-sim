<script>
  // Right-hand metrics tab: live values with sparklines, congestion meter,
  // baseline vs scenario comparison, time series and junction delays.
  import { app } from '../lib/store.svelte.js';
  import { congestionLabel, num, KPI_INFO } from '../lib/format.js';
  import Sparkline from './Sparkline.svelte';
  import UChart from './UChart.svelte';

  let tab = $state('live');
  const live = $derived(app.live);
  const congestion = $derived(live.stoppedShare ?? 0);
  const segs = $derived(Math.round(congestion * 10));
  const summary = $derived(app.result?.[app.variant]);
  const cmp = $derived(app.result?.comparison);
  const minutes = $derived(app.result?.baseline?.series.minute ?? []);
  const cursorMinute = $derived(live.minute >= 0 ? minutes[live.minute] : null);

  /** Chart series for one metric across the available variants. */
  function seriesOf(key, scale = 1) {
    const out = [{ label: 'Baseline', color: '#2ec4a5',
                   values: app.result.baseline.series[key].map((v) => v * scale) }];
    if (app.result.scenario) {
      out.push({ label: 'Scenario', color: '#f5a524',
                 values: app.result.scenario.series[key].map((v) => v * scale) });
    }
    return out;
  }

  /** KPI value: whole numbers from 100 up, one decimal below. */
  const fmt = (v) => num(v, v !== null && Math.abs(v) < 100 && !Number.isInteger(v) ? 1 : 0);

  /** Delta cell class: green when the change is an improvement. */
  function deltaClass(key, row) {
    if (row.delta_pct === null || row.delta_pct === 0) return '';
    const lowerBetter = KPI_INFO[key]?.[2];
    return (row.delta_pct < 0) === lowerBetter ? 'good' : 'bad';
  }

  const junctions = $derived((summary?.junctions ?? [])
    .filter((j) => j.delay_mean_s !== null).sort((a, b) => b.delay_mean_s - a.delay_mean_s).slice(0, 8));
</script>

<aside class="panel metrics">
  <div class="panel-head">
    <div class="tabs">
      <button class:on={tab === 'live'} onclick={() => (tab = 'live')}>Current</button>
      <button class:on={tab === 'compare'} onclick={() => (tab = 'compare')} disabled={!app.result}>Results</button>
    </div>
    <span class="muted">{tab === 'live' ? 'live · 1 s' : app.variant}</span>
  </div>

  {#if tab === 'live'}
    <div class="metric">
      <div><div class="label">Avg speed</div><div class="value">{num(live.meanKmh, 1)}<small>km/h</small></div></div>
      <Sparkline values={live.hist.speed} />
    </div>
    <div class="metric">
      <div><div class="label">Vehicles on road</div><div class="value">{num(live.count)}</div></div>
      <Sparkline values={live.hist.count} color="#c9d3e0" />
    </div>
    <div class="metric">
      <div><div class="label">Queued at junctions</div>
        <div class="value">{num(live.hist.queue.at(-1), 0)}<small>veh</small></div></div>
      <Sparkline values={live.hist.queue} color="var(--accent)" />
    </div>
    <div class="metric">
      <div><div class="label">Throughput</div>
        <div class="value">{num(live.hist.thr.at(-1))}<small>veh/h</small></div></div>
      <Sparkline values={live.hist.thr} color="#c9d3e0" />
    </div>
    <div class="metric congestion">
      <div class="row"><span class="label">Congestion level</span>
        <span class="level">{congestionLabel(congestion)} · {Math.round(congestion * 100)}%</span></div>
      <div class="segs">{#each Array(10) as _, i}<i class:on={i < segs} class:hot={i >= 6}></i>{/each}</div>
      <div class="row muted small"><span>Free</span><span>Gridlock</span></div>
    </div>
    {#if live.minute === -1 && app.result}
      <p class="note">Warm-up: queue, throughput and road colours start after {app.request?.warmup_s}s.</p>
    {/if}
  {:else if app.result}
    {#if cmp}
      <table>
        <thead><tr><th></th><th>Baseline</th><th>Scenario</th><th>Δ</th></tr></thead>
        <tbody>
          {#each Object.entries(KPI_INFO) as [key, [label, unit]]}
            {@const row = cmp.kpi[key]}
            {#if row}
              <tr><td>{label}{unit ? ` (${unit})` : ''}</td><td>{fmt(row.baseline)}</td><td>{fmt(row.scenario)}</td>
                <td class={deltaClass(key, row)}>{row.delta_pct === null ? '—' : `${row.delta_pct > 0 ? '+' : ''}${row.delta_pct}%`}</td></tr>
            {/if}
          {/each}
        </tbody>
      </table>
    {:else}
      <table>
        <tbody>
          {#each Object.entries(KPI_INFO) as [key, [label, unit]]}
            <tr><td>{label}{unit ? ` (${unit})` : ''}</td><td>{fmt(app.result.baseline.kpi[key])}</td></tr>
          {/each}
        </tbody>
      </table>
    {/if}
    <h4>Throughput per hour</h4>
    <UChart x={minutes} series={seriesOf('throughput_per_min', 60)} cursorX={cursorMinute} />
    <h4>Vehicles queued</h4>
    <UChart x={minutes} series={seriesOf('queue_total')} cursorX={cursorMinute} />
    <h4>Slowest junctions <span class="muted">({app.variant}, mean approach delay)</span></h4>
    <table>
      <thead><tr><th>Junction</th><th>Delay s</th><th>Veh</th><th>Max queue</th></tr></thead>
      <tbody>
        {#each junctions as j}
          <tr><td><button class="jn" title="Show on the map" onclick={() => (app.focusNode = j.node)}>{j.label}</button>
            <span class="muted">{j.control === 'circle' ? 'circle' : j.signalised ? 'signal' : 'give way'}</span></td>
            <td>{num(j.delay_mean_s, 1)}</td><td>{j.vehicles}</td><td>{j.queue_max_veh}</td></tr>
        {/each}
      </tbody>
    </table>
    {#if app.result.info?.edit_usage?.length}
      <h4>Scenario edits <span class="muted">(what the vehicles did)</span></h4>
      <ul class="usage">
        {#each app.result.info.edit_usage as u}
          <li><b>{u.junction ? `${u.junction}: ` : ''}{u.label}</b><span>{u.text}</span></li>
        {/each}
      </ul>
    {/if}
  {/if}
</aside>

<style>
  .metrics { overflow-y: auto; }
  .jn { background: none; border: 0; padding: 0; color: var(--text); font: 600 12px var(--mono);
        text-decoration: underline dotted; text-underline-offset: 3px; margin-right: 6px; }
  .usage { list-style: none; margin: 0; padding: 0 16px 16px; display: grid; gap: 6px; font-size: 12.5px; }
  .usage li { display: grid; gap: 1px; background: var(--panel-2); border-radius: 6px; padding: 6px 8px; }
  .usage span { color: var(--muted); }
  .tabs { display: flex; gap: 4px; }
  .tabs button { background: none; border: 0; color: var(--muted); font-weight: 600; padding: 0 8px 0 0; }
  .tabs button.on { color: var(--text); }
  .metric { display: flex; justify-content: space-between; align-items: center; gap: 8px;
            padding: 14px 16px; border-bottom: 1px solid var(--line); }
  .label { font-size: 12.5px; color: var(--muted); }
  .value { font-family: var(--mono); font-size: 22px; font-weight: 500; margin-top: 4px; }
  .value small { font-size: 12px; color: var(--muted); margin-left: 6px; }
  .congestion { display: grid; gap: 8px; justify-content: stretch; align-items: stretch; }
  .row { display: flex; justify-content: space-between; }
  .level { color: var(--accent); font-weight: 600; font-size: 13px; }
  .segs { display: grid; grid-template-columns: repeat(10, 1fr); gap: 3px; }
  .segs i { height: 8px; border-radius: 2px; background: var(--line); }
  .segs i.on { background: var(--free); }
  .segs i.on.hot { background: var(--accent); }
  .small { font-size: 12px; }
  .note { font-size: 12px; color: var(--muted); padding: 0 16px; }
  table { width: calc(100% - 24px); margin: 10px 12px; border-collapse: collapse; font-size: 12.5px; }
  th { text-align: right; color: var(--muted); font-weight: 500; padding: 4px; }
  td { text-align: right; padding: 5px 4px; font-family: var(--mono); border-top: 1px solid var(--line); }
  td:first-child, th:first-child { text-align: left; font-family: var(--sans); }
  .good { color: var(--free); }
  .bad { color: var(--jam); }
  h4 { margin: 16px 16px 6px; font-size: 12.5px; font-weight: 600; }
  h4 .muted { font-weight: 400; }
  .metrics :global(.uchart) { padding: 0 8px; box-sizing: border-box; }
</style>
