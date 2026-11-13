<script>
  // uPlot line chart that follows its container width.
  import { onMount } from 'svelte';
  import uPlot from 'uplot';

  /**
   * @type {{x: number[], series: {label: string, values: (number|null)[], color: string}[],
   *         height?: number, unit?: string, cursorX?: number|null}}
   */
  let { x, series, height = 140, unit = '', cursorX = null } = $props();
  let el;
  let plot = null;

  /** uPlot options for the current series list. */
  function options(width) {
    const axis = { stroke: '#7d8aa0', grid: { stroke: '#1f2836', width: 1 }, ticks: { show: false },
                   font: '11px "IBM Plex Mono", monospace' };
    return {
      width, height, legend: { show: true, live: false },
      cursor: { drag: { x: false, y: false } },
      scales: { x: { time: false } },
      axes: [{ ...axis, values: (_, v) => v.map((m) => `${m}m`) },
             { ...axis, size: 44, values: (_, v) => v.map((n) => `${n}${unit}`) }],
      series: [{}, ...series.map((s) => ({ label: s.label, stroke: s.color, width: 1.8, spanGaps: true,
                                           points: { show: false } }))],
      hooks: { draw: [drawCursor] },
    };
  }

  /** Vertical marker at the replay's current minute. */
  function drawCursor(u) {
    if (cursorX === null || cursorX === undefined) return;
    const px = u.valToPos(cursorX, 'x', true);
    const { ctx, bbox } = u;
    ctx.save();
    ctx.strokeStyle = 'rgba(245,165,36,0.7)';
    ctx.lineWidth = 1.5 * devicePixelRatio;
    ctx.beginPath(); ctx.moveTo(px, bbox.top); ctx.lineTo(px, bbox.top + bbox.height); ctx.stroke();
    ctx.restore();
  }

  function rebuild() {
    plot?.destroy();
    plot = new uPlot(options(el.clientWidth || 280), [x, ...series.map((s) => s.values)], el);
  }

  onMount(() => {
    const ro = new ResizeObserver(() => plot?.setSize({ width: el.clientWidth, height }));
    ro.observe(el);
    return () => { ro.disconnect(); plot?.destroy(); };
  });

  // Rebuild on data change; just redraw when only the cursor moves.
  $effect(() => { x; series; if (el) rebuild(); });
  $effect(() => { cursorX; plot?.redraw(false, false); });
</script>

<div class="uchart" bind:this={el}></div>

<style>
  .uchart { width: 100%; }
  .uchart :global(.u-legend) { font-size: 11px; color: var(--muted); text-align: left; padding-top: 4px; }
  .uchart :global(.u-legend .u-marker) { width: 10px; height: 3px; border: 0 !important; }
</style>
