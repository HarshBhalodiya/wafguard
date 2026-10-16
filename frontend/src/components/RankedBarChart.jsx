import { charts } from "../theme.js";
import { EmptyState } from "./LoadingState.jsx";

/**
 * Shared rendering for RootCauseChart and SHAPChart (spec section 33 lists
 * them separately, but both are "ranked contributors with a percentage
 * bar" — one component avoids re-implementing the same bar math twice).
 */
export default function RankedBarChart({ items, labelKey = "label", valueKey = "value", barColor = charts.series.accent, emptyLabel }) {
  if (!items || items.length === 0) return <EmptyState label={emptyLabel || "No data yet."} />;

  const max = Math.max(...items.map((i) => i[valueKey]), 0.0001);

  return (
    <div className="space-y-2.5">
      {items.map((item, idx) => {
        const pct = Math.round(item[valueKey] * 100);
        const widthPct = Math.max(2, (item[valueKey] / max) * 100);
        return (
          <div key={item[labelKey] + idx}>
            <div className="flex items-center justify-between text-xs mb-1">
              <span className="text-ink-primary font-medium">{item[labelKey]}</span>
              <span className="text-ink-secondary tabular-nums">{pct}%</span>
            </div>
            {/* Squared ends: these are measurement bars, not pills. */}
            <div className="h-2 rounded-sm bg-surface-raised overflow-hidden">
              <div className="h-full rounded-sm" style={{ width: `${widthPct}%`, backgroundColor: item.color || barColor }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}
