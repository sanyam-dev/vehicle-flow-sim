<script>
  // Replay controls: restart, play/pause, step, seekable timeline, speed.
  import { player } from '../lib/player.svelte.js';
  import { hms } from '../lib/format.js';

  const SPEEDS = [0.5, 1, 2, 4, 8];
  const pct = $derived(player.duration ? (player.t / player.duration) * 100 : 0);
  const disabled = $derived(!player.duration);

  /** Seek from a click/drag on the timeline track. */
  function seekFrom(e) {
    const r = e.currentTarget.getBoundingClientRect();
    player.seek(((e.clientX - r.left) / r.width) * player.duration);
  }

  function onPointer(e) {
    if (disabled) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    seekFrom(e);
  }

  function onKey(e) {
    if (e.target.closest('input, textarea, select')) return;
    if (e.code === 'Space') { e.preventDefault(); player.toggle(); }
    if (e.code === 'ArrowRight') player.seek(player.t + 10);
    if (e.code === 'ArrowLeft') player.seek(player.t - 10);
  }
</script>

<svelte:window onkeydown={onKey} />

<div class="bar">
  <button class="icon" title="Restart" {disabled} onclick={() => player.seek(0)} aria-label="Restart">
    <svg viewBox="0 0 24 24" width="18" height="18"><path d="M4 12a8 8 0 1 0 2.3-5.6M4 4v4h4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
  </button>
  <button class="play" {disabled} onclick={() => player.toggle()} aria-label={player.playing ? 'Pause' : 'Play'}>
    {#if player.playing}
      <svg viewBox="0 0 24 24" width="20" height="20"><rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor"/><rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor"/></svg>
    {:else}
      <svg viewBox="0 0 24 24" width="20" height="20"><path d="M8 5v14l11-7z" fill="currentColor"/></svg>
    {/if}
  </button>
  <button class="icon" title="Step one frame" {disabled} onclick={() => player.step()} aria-label="Step">
    <svg viewBox="0 0 24 24" width="18" height="18"><path d="M6 5v14l9-7zM18 5v14" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>
  </button>
  <span class="time">{hms(player.t)}</span>
  <div class="track" class:disabled role="slider" tabindex="0" aria-label="Timeline"
       aria-valuemin="0" aria-valuemax={player.duration} aria-valuenow={player.t}
       onpointerdown={onPointer} onpointermove={(e) => e.buttons && !disabled && seekFrom(e)}>
    <div class="fill" style:width="{pct}%"></div>
    <div class="knob" style:left="{pct}%"></div>
  </div>
  <span class="time muted">{hms(player.duration)}</span>
  {#if player.buffering}<span class="buf">loading…</span>{/if}
  <div class="speeds">
    {#each SPEEDS as s}
      <button class:on={player.speed === s} onclick={() => (player.speed = s)}>{s}×</button>
    {/each}
  </div>
</div>

<style>
  .bar { display: flex; align-items: center; gap: 14px; padding: 12px 16px; border: 1px solid var(--line);
         border-radius: var(--radius); background: var(--panel); flex-wrap: wrap; }
  .icon { background: none; border: 0; color: var(--muted); padding: 4px; display: grid; }
  .icon:hover:not(:disabled) { color: var(--text); }
  .play { width: 48px; height: 48px; border-radius: 50%; background: var(--accent); color: #141414;
          border: 0; display: grid; place-items: center; }
  .play:disabled { opacity: 0.4; }
  .time { font-family: var(--mono); font-size: 13px; }
  .muted { color: var(--muted); }
  .track { flex: 1; min-width: 120px; height: 18px; position: relative; cursor: pointer; touch-action: none; }
  .track::before { content: ''; position: absolute; left: 0; right: 0; top: 7px; height: 4px;
                   border-radius: 2px; background: var(--line); }
  .fill { position: absolute; left: 0; top: 7px; height: 4px; border-radius: 2px; background: var(--accent); }
  .knob { position: absolute; top: 3px; width: 12px; height: 12px; margin-left: -6px; border-radius: 50%;
          background: var(--accent); box-shadow: 0 0 0 3px rgba(245, 165, 36, 0.2); }
  .track.disabled { cursor: default; opacity: 0.4; }
  .buf { font-size: 11px; color: var(--muted); }
  .speeds { display: flex; gap: 2px; background: var(--panel-2); border-radius: 8px; padding: 3px; }
  .speeds button { background: none; border: 0; color: var(--muted); font-family: var(--mono);
                   font-size: 12px; padding: 7px 10px; border-radius: 6px; }
  .speeds button.on { background: var(--line); color: var(--text); }
</style>
