/**
 * WafGuard palette — the single source of truth for colour.
 *
 * `tailwind.config.js` imports this (`palette`) so utilities like
 * `bg-surface-panel` and `text-ink-secondary` resolve to the same values the
 * charts read (`charts`). Before this module, chart components hard-coded their
 * own hexes and drifted from the CSS theme; keep every new colour here.
 *
 * Design rules:
 * - Surfaces are NEUTRAL graphite: no blue cast, no purple, no gradients. This
 *   is an operator tool that runs all day next to fab equipment, not a landing
 *   page, so the chrome stays quiet and the data carries the colour.
 * - One accent (`brand`: desaturated steel blue) marks interactive and selected
 *   state. It is deliberately not the neon cyan/violet most generated UIs ship.
 * - Green / amber / red are SEMANTIC, not decoration: they encode yield, risk
 *   and tool health. They keep their conventional hue (operators already read
 *   green = good, amber = watch, red = act) and only the saturation is tamed.
 * - Severity colours must stay distinguishable from the accent, so the accent
 *   never borrows green/amber/red.
 */
export const palette = {
  // Surfaces: neutral graphite, ordered base -> raised.
  surface: {
    DEFAULT: "#0f1113",
    panel: "#16191c",
    raised: "#1e2226",
    border: "#2b3137",
    strong: "#3b434b",
  },
  // Text tones.
  ink: {
    primary: "#e7ebee",
    secondary: "#98a2ab",
    muted: "#6a747d",
  },
  // Interactive accent — desaturated steel blue.
  brand: {
    DEFAULT: "#4f93d1",
    dim: "#2c5f8b",
  },
  // Severity / risk semantics (unchanged meaning, calmer rendering).
  risk: {
    low: "#46a758",
    medium: "#d8a13b",
    high: "#e5484d",
  },
  status: {
    healthy: "#46a758",
    warning: "#d8a13b",
    critical: "#e5484d",
    info: "#8ab0c9",
  },
};

/**
 * Chart tokens. Recharts and Plotly take literal colours rather than Tailwind
 * classes, so charts read them from here instead of repeating hexes.
 */
export const charts = {
  grid: palette.surface.border,
  axis: palette.ink.secondary,
  tooltip: {
    background: palette.surface.panel,
    border: palette.surface.border,
    text: palette.ink.primary,
  },
  /** One entry per meaning, not per series — a line's colour should say something. */
  series: {
    accent: palette.brand.DEFAULT,
    neutral: "#8a94a0",
    positive: palette.risk.low,
    warning: palette.risk.medium,
    negative: palette.risk.high,
  },
  /** Area fills are flat low-opacity blocks: no gradient fades under the lines. */
  fillOpacity: 0.12,
  /** Plotly heatmaps (wafer maps / Grad-CAM attention). */
  colorscale: {
    outside: palette.surface.DEFAULT,
    pass: palette.risk.low,
    fail: palette.risk.high,
    attentionMid: palette.risk.medium,
    attentionHigh: palette.risk.high,
  },
};

export default palette;
