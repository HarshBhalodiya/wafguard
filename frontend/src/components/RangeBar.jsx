/**
 * Shows an uncertainty range instead of a single number (Phase 10).
 * `band` = {p10, p50, p90, interval_80?}. Values are percentages of yield or
 * percentage-point differences, so the scale is passed in by the caller.
 */
export default function RangeBar({ label, band, unit = "%", min = 50, max = 100 }) {
  if (!band) return null;
  const clamp = (v) => Math.max(0, Math.min(100, ((v - min) / (max - min)) * 100));
  const [lo, hi] = band.interval_80 || [band.p10, band.p90];
  return (
    <div className="space-y-1" data-testid={`range-${label}`}>
      <div className="flex items-baseline justify-between text-xs">
        <span className="text-ink-secondary">{label}</span>
        <span className="font-mono text-ink-primary">
          {band.p50}
          {unit} <span className="text-ink-muted">(P10 {band.p10} – P90 {band.p90})</span>
        </span>
      </div>
      <div className="relative h-2 rounded bg-surface-raised">
        <div className="absolute h-2 rounded bg-brand/20" style={{ left: `${clamp(lo)}%`, width: `${Math.max(1, clamp(hi) - clamp(lo))}%` }} />
        <div className="absolute h-2 rounded bg-brand/60" style={{ left: `${clamp(band.p10)}%`, width: `${Math.max(1, clamp(band.p90) - clamp(band.p10))}%` }} />
        <div className="absolute h-3 -top-0.5 w-0.5 bg-ink-primary" style={{ left: `${clamp(band.p50)}%` }} />
      </div>
      {band.interval_80 && (
        <div className="text-[11px] text-ink-muted">
          80% outcome interval: {lo}
          {unit} – {hi}
          {unit}
        </div>
      )}
    </div>
  );
}
