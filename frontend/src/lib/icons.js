// Top-down vector vehicle icons drawn on a canvas (pointing north, heading 0).
// Rasterised once to canvases that the vehicle layer stamps (rotated) each frame.

const RATIO = 2; // device pixels per icon pixel

/** Rounded-rectangle path helper. */
function rrect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, r);
}

const DRAW = {
  /** Two-wheeler: slim body, handlebar, rider. */
  bike(ctx) {
    ctx.fillStyle = '#67e8f9';
    rrect(ctx, 3, 1, 4, 18, 2); ctx.fill();
    ctx.fillRect(0.5, 4, 9, 1.6);
    ctx.fillStyle = '#0b1220';
    ctx.beginPath(); ctx.arc(5, 10, 2.1, 0, Math.PI * 2); ctx.fill();
    return [10, 20];
  },
  /** Auto-rickshaw: rounded nose, yellow canopy on a green body. */
  auto(ctx) {
    ctx.fillStyle = '#84cc16';
    rrect(ctx, 1, 1, 12, 18, [6, 6, 2, 2]); ctx.fill();
    ctx.fillStyle = '#facc15';
    rrect(ctx, 2.5, 6, 9, 11.5, 1.5); ctx.fill();
    ctx.fillStyle = '#0b1220';
    ctx.fillRect(3.5, 3, 7, 2.2);
    return [14, 20];
  },
  /** Car: light body, dark windscreen and rear window. */
  car(ctx) {
    ctx.fillStyle = '#e6ebf2';
    rrect(ctx, 1, 1, 12, 24, 3); ctx.fill();
    ctx.fillStyle = '#1f2a3a';
    rrect(ctx, 2.5, 6, 9, 4.5, 1.2); ctx.fill();
    rrect(ctx, 2.5, 18, 9, 3, 1.2); ctx.fill();
    return [14, 26];
  },
  /** Truck / bus: long blue body with a separate cab. */
  truck(ctx) {
    ctx.fillStyle = '#7aa7ff';
    rrect(ctx, 1, 1, 14, 9, 2.5); ctx.fill();
    ctx.fillStyle = '#4f7fe0';
    rrect(ctx, 1, 11, 14, 27, 1.5); ctx.fill();
    ctx.fillStyle = '#0b1220';
    ctx.fillRect(3, 3, 10, 2.5);
    return [16, 39];
  },
};

/**
 * Rasterise one icon at RATIO device pixels per icon pixel.
 * @param {keyof DRAW} name
 * @returns {{canvas: HTMLCanvasElement, w: number, h: number}} w/h in icon pixels
 */
function render(name) {
  const probe = document.createElement('canvas').getContext('2d');
  const [w, h] = DRAW[name](probe); // dry run to learn the size
  const canvas = document.createElement('canvas');
  canvas.width = w * RATIO;
  canvas.height = h * RATIO;
  const ctx = canvas.getContext('2d');
  ctx.scale(RATIO, RATIO);
  DRAW[name](ctx);
  return { canvas, w, h };
}

/** All vehicle icons: {bike, auto, car, truck} -> {canvas, w, h}. */
export function vehicleIcons() {
  return Object.fromEntries(Object.keys(DRAW).map((name) => [name, render(name)]));
}

/** Small inline SVG markup for the legend (same shapes, simplified). */
export const LEGEND_SVG = {
  car: '<svg viewBox="0 0 26 14" width="22" height="12"><rect x="1" y="1" width="24" height="12" rx="3" fill="#e6ebf2"/><rect x="15" y="2.5" width="4.5" height="9" rx="1" fill="#1f2a3a"/><rect x="4" y="2.5" width="3" height="9" rx="1" fill="#1f2a3a"/></svg>',
  bike: '<svg viewBox="0 0 20 10" width="18" height="10"><rect x="1" y="3" width="18" height="4" rx="2" fill="#67e8f9"/><rect x="14.5" y="0.5" width="1.6" height="9" fill="#67e8f9"/></svg>',
  auto: '<svg viewBox="0 0 20 14" width="16" height="12"><rect x="1" y="1" width="18" height="12" rx="3" fill="#84cc16"/><rect x="3" y="2.5" width="11" height="9" rx="1.5" fill="#facc15"/></svg>',
  truck: '<svg viewBox="0 0 39 16" width="28" height="12"><rect x="1" y="1" width="27" height="14" rx="1.5" fill="#4f7fe0"/><rect x="29" y="1" width="9" height="14" rx="2.5" fill="#7aa7ff"/></svg>',
};
