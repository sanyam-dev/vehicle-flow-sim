<script>
  // Map legend: what road colours, outlines, junction marks and vehicle icons mean.
  import { LEGEND_SVG } from '../lib/icons.js';
  import { COLORS } from '../lib/maplayers.js';

  const speeds = [['Slow', COLORS.slow], ['Jammed', COLORS.jam]];
  const vehicles = [['Car', 'car'], ['Bike', 'bike'], ['Auto', 'auto'], ['Truck/Bus', 'truck']];
  let open = $state(true);
</script>

<div class="legend" class:closed={!open}>
  <button class="toggle" onclick={() => (open = !open)} aria-label="Toggle legend" aria-expanded={open}>
    {open ? '×' : 'Legend'}
  </button>
  {#if open}
    <div class="group"><span class="title">Roads</span>
      <span class="item"><i class="road" style:background={COLORS.roadMajor}></i>Road</span>
      <span class="item"><i class="road over" style:background={COLORS.roadMajor}
                           style:border-color={COLORS.bridge}></i>Flyover</span>
      <span class="item"><i class="road under" style:background={COLORS.roadUnder}
                           style:border-color={COLORS.underpass}></i>Underpass</span>
      {#each speeds as [label, c]}<span class="item"><i class="road" style:background={c}></i>{label}</span>{/each}
      <span class="item"><i class="road dash"></i>Closed</span>
      <span class="item"><i class="road over" style:background={COLORS.roadMajor}
                           style:border-color={COLORS.edit}></i>Added / widened</span>
    </div>
    <div class="group"><span class="title">Junctions</span>
      <span class="item"><i class="dot" style:background={COLORS.light}></i>Signal</span>
      <span class="item"><i class="lights"><b style:background={COLORS.jam}></b><b style:background={COLORS.slow}></b><b style:background={COLORS.free}></b></i>Stop bar</span>
      <span class="item"><i class="ring" style:background={COLORS.island}
                           style:border-color={COLORS.light}></i>Circle</span>
      <span class="item"><i class="dot small" style:background={COLORS.junction}></i>Give way</span>
      <span class="item"><i class="dot" style:background={COLORS.slow}></i><i class="dot" style:background={COLORS.jam}></i>Queue building / long</span>
    </div>
    <div class="group"><span class="title">Vehicles</span>
      {#each vehicles as [label, key]}<span class="item">{@html LEGEND_SVG[key]}{label}</span>{/each}
    </div>
  {/if}
</div>

<style>
  .legend { position: absolute; left: 14px; bottom: 14px; right: 14px; max-width: max-content;
            display: grid; gap: 5px; background: var(--glass); border: 1px solid var(--line);
            border-radius: 10px; padding: 8px 34px 8px 12px; font-size: 11.5px; color: var(--text-2);
            backdrop-filter: blur(6px); }
  .legend.closed { padding: 0; right: auto; }
  .toggle { position: absolute; top: 4px; right: 6px; background: none; border: 0; color: var(--muted);
            font-size: 14px; padding: 2px 6px; }
  .closed .toggle { position: static; font-size: 12px; padding: 8px 12px; color: var(--text); }
  .group { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 12px; }
  .title { width: 62px; font-family: var(--mono); font-size: 10px; letter-spacing: 0.1em;
           text-transform: uppercase; color: var(--muted); }
  .item { display: inline-flex; align-items: center; gap: 5px; white-space: nowrap; }
  .road { width: 18px; height: 5px; border-radius: 1px; display: inline-block; box-sizing: content-box; }
  .road.over { border-top: 2px solid; border-bottom: 2px solid; }
  .road.under { border-top: 2px dashed; border-bottom: 2px dashed; }
  .road.dash { background: repeating-linear-gradient(90deg, #ef4444 0 4px, transparent 4px 7px); }
  .dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
  .dot.small { width: 6px; height: 6px; }
  .ring { width: 9px; height: 9px; border-radius: 50%; border: 2px solid; display: inline-block; }
  .lights { display: inline-flex; gap: 1px; }
  .lights b { width: 5px; height: 4px; border-radius: 1px; }
</style>
