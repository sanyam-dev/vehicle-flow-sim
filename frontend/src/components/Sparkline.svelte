<script>
  // Tiny inline SVG trend line (nulls are skipped).
  /** @type {{values: (number|null)[], color?: string}} */
  let { values, color = 'var(--free)' } = $props();
  const W = 88;
  const H = 26;

  const d = $derived.by(() => {
    const pts = values.map((v, i) => [i, v]).filter(([, v]) => v !== null && Number.isFinite(v));
    if (pts.length < 2) return '';
    const ys = pts.map(([, v]) => v);
    const lo = Math.min(...ys);
    const span = Math.max(...ys) - lo || 1;
    const n = Math.max(values.length - 1, 1);
    return pts.map(([i, v], k) =>
      `${k ? 'L' : 'M'}${((i / n) * W).toFixed(1)},${(H - 2 - ((v - lo) / span) * (H - 4)).toFixed(1)}`).join('');
  });
</script>

<svg viewBox="0 0 {W} {H}" width={W} height={H} aria-hidden="true">
  <path {d} fill="none" stroke={color} stroke-width="1.6" stroke-linejoin="round" />
</svg>
