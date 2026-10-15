import { useApi } from "../hooks/useApi";
import { get } from "../services/api";
import Panel from "../components/Panel.jsx";
import RiskBadge from "../components/RiskBadge.jsx";
import SyntheticNotice from "../components/SyntheticNotice.jsx";
import { EmptyState, ErrorState, LoadingState } from "../components/LoadingState.jsx";
import { formatDate, formatPercent, formatYield } from "../utils/format";

/**
 * Upcoming Lots (Phase 10): what is about to run, sorted by pre-run risk.
 * The queue is derived (one hypothetical next run per tool) — the page says so.
 */
export default function UpcomingLots() {
  const { data, loading, error, reload } = useApi(() => get("/lot-risk/upcoming", { limit: 50 }), []);

  if (loading) return <LoadingState label="Scoring upcoming runs…" />;
  if (error) return <ErrorState error={error} onRetry={reload} />;

  const rows = data?.lots || [];
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-ink-primary">Upcoming lots</h1>
        <p className="text-xs text-ink-muted">Planned start {formatDate(data.planned_start)} · sorted by risk · {data.label}</p>
      </div>
      <SyntheticNotice>{data.queue_note}</SyntheticNotice>
      <Panel title={`${data.n_tools_scored} tools scored`} subtitle={data.n_tools_skipped ? `${data.n_tools_skipped} skipped (no prior run to estimate from)` : undefined}>
        {rows.length === 0 ? (
          <EmptyState label="No tool has enough history to score a next run." />
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left label-caps">
                <th className="py-2">Tool</th>
                <th>Recipe / step</th>
                <th>Risk</th>
                <th>P(low yield)</th>
                <th>Predicted yield</th>
                <th>Main reason</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-surface-border">
              {rows.map((row) => (
                <tr key={row.equipment_id} data-testid="upcoming-row">
                  <td className="py-2 font-mono text-ink-primary">{row.equipment_id}</td>
                  <td className="text-ink-secondary">{row.recipe_id} · {row.process_step}</td>
                  <td><RiskBadge level={row.risk_level} probability={row.risk_probability} size="sm" /></td>
                  <td className="font-mono">{formatPercent(row.probability_low_yield, 0)}</td>
                  <td className="font-mono">{formatYield(row.predicted_yield)}</td>
                  <td className="text-xs text-ink-secondary">{row.main_reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  );
}
