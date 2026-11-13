<script>
  // Map legend: road speed colours, grade separation, vehicle icons.
  import { LEGEND_SVG } from '../lib/icons.js';
  import { COLORS } from '../lib/maplayers.js';

  const roads = [['Free flow', COLORS.free], ['Slow', COLORS.slow], ['Jammed', COLORS.jam],
                 ['No data', COLORS.nodata]];
  const grades = [['Flyover', COLORS.bridge], ['Underpass', COLORS.underpass]];
  const vehicles = [['Car', 'car'], ['Bike', 'bike'], ['Auto', 'auto'], ['Truck/Bus', 'truck']];
  let open = $state(true);
</script>

<div class="legend" class:closed={!open}>
  <button class="toggle" onclick={() => (open = !open)} aria-label="Toggle legend">
    {open ? '×' : 'Legend'}
  </button>
  {#if open}
    <div class="group">
      {#each roads as [label, c]}<span class="item"><i class="bar" style:background={c}></i>{label}</span>{/each}
    </div>
    <div class="group">
      {#each grades as [label, c]}<span class="item"><i class="ring" style:border-color={c}></i>{label}</span>{/each}
      <span class="item"><i class="bar dash"></i>Closed</span>
    </div>
    <div class="group">
      {#each vehicles as [label, key]}<span class="item">{@html LEGEND_SVG[key]}{label}</span>{/each}
    </div>
    <div class="group">
      <span class="item"><i class="dot" style:background={COLORS.free}></i>Junction queue: short</span>
      <span class="item"><i class="dot" style:background={COLORS.slow}></i>building</span>
      <span class="item"><i class="dot" style:background={COLORS.jam}></i>long</span>
    </div>
  {/if}
</div>

<style>
  .legend { position: absolute; left: 14px; bottom: 14px; right: 14px; max-width: max-content;
            display: flex; flex-wrap: wrap; align-items: center; gap: 6px 18px;
            background: rgba(17, 23, 32, 0.92); border: 1px solid var(--line); border-radius: 8px;
            padding: 8px 36px 8px 12px; font-size: 12px; }
  .legend.closed { padding: 0; right: auto; }
  .toggle { position: absolute; top: 4px; right: 6px; background: none; border: 0; color: var(--muted);
            font-size: 14px; padding: 2px 6px; }
  .closed .toggle { position: static; font-size: 12px; padding: 8px 12px; color: var(--text); }
  .group { display: flex; flex-wrap: wrap; gap: 6px 12px; }
  .group + .group { border-left: 1px solid var(--line); padding-left: 14px; }
  .item { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
  .bar { width: 16px; height: 4px; border-radius: 2px; display: inline-block; }
  .bar.dash { background: repeating-linear-gradient(90deg, #ef4444 0 4px, transparent 4px 7px); }
  .ring { width: 14px; height: 8px; border: 2px solid; border-radius: 3px; display: inline-block; }
  .dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
</style>
