<script>
  // Map editor: a clicked point lies where roads are stacked (a road with a
  // flyover over it or an underpass below). Lists each road next to the point,
  // ground road first and preselected; the user confirms one.
  import { COLORS } from '../lib/maplayers.js';

  /**
   * @type {{options: {name: string, grade: string}[], index: number, x: number, y: number,
   *         onpick: (i: number) => void, onconfirm: () => void, oncancel: () => void}}
   *   x / y: the chosen point in map-container pixels
   */
  let { options, index, x, y, onpick, onconfirm, oncancel } = $props();

  const LEVELS = { road: ['Road', COLORS.roadMajor], bridge: ['Flyover', COLORS.bridge],
                   underpass: ['Underpass', COLORS.underpass] };
  let box = $state(null);
  let w = $state(0);
  let h = $state(0);

  // Open to the right of the point, flipping left / up when it would leave the map.
  const left = $derived.by(() => {
    const room = box?.parentElement?.clientWidth ?? Infinity;
    return x + 16 + w > room ? Math.max(8, x - 16 - w) : x + 16;
  });
  const top = $derived.by(() => {
    const room = box?.parentElement?.clientHeight ?? Infinity;
    return Math.max(8, Math.min(y - 20, room - h - 8));
  });

  $effect(() => { box?.querySelector('button.on')?.focus(); });
</script>

<div class="chooser" role="dialog" aria-label="Choose the road at this point" bind:this={box}
     bind:clientWidth={w} bind:clientHeight={h} style:left="{left}px" style:top="{top}px">
  <div class="head">Roads are stacked here. Snap to:</div>
  <div class="opts" role="radiogroup" aria-label="Road at this point">
    {#each options as o, i}
      <button role="radio" aria-checked={i === index} class:on={i === index}
              onclick={() => onpick(i)} ondblclick={onconfirm}>
        <i style:background={LEVELS[o.grade][1]}></i>
        <span class="lvl">{LEVELS[o.grade][0]}</span>
        <span class="nm">{o.name}</span>
      </button>
    {/each}
  </div>
  <div class="row">
    <button class="btn primary" onclick={onconfirm}>Use this point</button>
    <button class="btn" onclick={oncancel}>Cancel</button>
  </div>
  <div class="hint">↑ ↓ to choose · Enter to confirm · Esc to cancel</div>
</div>

<style>
  .chooser { position: absolute; z-index: 5; min-width: 200px; max-width: 280px; display: grid; gap: 8px;
             background: var(--glass); border: 1px solid var(--line-strong); border-radius: 10px;
             padding: 10px; backdrop-filter: blur(6px); font-size: 12.5px; }
  .head { color: var(--text-2); }
  .opts { display: grid; gap: 4px; }
  .opts button { display: flex; align-items: center; gap: 8px; text-align: left; padding: 6px 8px;
                 border-radius: 7px; border: 1px solid var(--line); background: none; color: var(--text-2); }
  .opts button.on { border-color: var(--accent); color: var(--text); background: rgba(255, 255, 255, 0.04); }
  .opts i { width: 14px; height: 6px; border-radius: 2px; flex: none; }
  .lvl { font-weight: 600; }
  .nm { color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .row { display: flex; gap: 6px; }
  .hint { color: var(--muted); font-size: 11px; }
</style>
