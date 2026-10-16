import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApi } from "../hooks/useApi";
import { get } from "../services/api";
import Panel from "../components/Panel.jsx";
import MetricCard from "../components/MetricCard.jsx";
import { charts } from "../theme.js";
import SyntheticNotice from "../components/SyntheticNotice.jsx";
import { EmptyState, ErrorState, LoadingState } from "../components/LoadingState.jsx";
import { formatPercent } from "../utils/format";

const BAND_CLASS = {
  significant: "text-risk-high",
  moderate: "text-risk-medium",
  stable: "text-risk-low",
};

/** Model & Data Health (Phase 10): drift, model monitoring and ingest data quality. */
export default function ModelDataHealth() {
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-ink-primary">Model & data health</h1>
        <p className="text-xs text-ink-muted">Is the data still like the training data, and are the models still right?</p>
      </div>
      <SyntheticNotice />
      <MonitoringPanel />
      <div className="grid lg:grid-cols-2 gap-4">
        <DriftPanel />
        <QualityPanel />
      </div>
    </div>
  );
}

function MonitoringPanel() {
  const { data, loading, error, reload } = useApi(() => get("/quality/monitoring", { limit: 90 }), []);
  if (loading) return <LoadingState label="Loading monitoring snapshots…" />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  const s = data.summary || {};
  if (!s.n_days) return <EmptyState label={data.note || "No monitoring snapshots yet."} />;
  const series = data.snapshots.map((snap) => ({
    date: snap.date,
    meanProbability: snap.prediction_distribution.mean_probability_low_yield,
    ece: snap.calibration.ece,
  }));
  return (
    <Panel title="Model monitoring" subtitle={`${s.n_days} daily snapshots · ${s.first_day} → ${s.last_day} · ${data.label}`}>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <MetricCard label="Lots predicted" value={s.n_predicted} />
        <MetricCard label="False alarms" value={s.total_false_alarms} sublabel={`${formatPercent(s.false_alarm_rate_of_flags, 0)} of flags`} />
        <MetricCard label="Missed low-yield lots" value={s.total_missed_low_yield} sublabel={`${formatPercent(s.miss_rate_of_low_yield, 0)} of low-yield lots`} />
        <MetricCard label="Latest calibration error (ECE)" value={s.latest_ece ?? "—"} />
      </div>
      <div className="h-48">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={series}>
            <CartesianGrid strokeDasharray="3 3" stroke={charts.grid} />
            <XAxis dataKey="date" tick={{ fontSize: 10, fill: charts.axis }} minTickGap={24} />
            <YAxis tick={{ fontSize: 10, fill: charts.axis }} domain={[0, "auto"]} />
            <Tooltip
              contentStyle={{ background: charts.tooltip.background, border: `1px solid ${charts.tooltip.border}`, borderRadius: 6, fontSize: 12 }}
              labelStyle={{ color: charts.tooltip.text }}
            />
            <Line type="monotone" dataKey="meanProbability" name="mean P(low yield)" dot={false} stroke={charts.series.accent} />
            <Line type="monotone" dataKey="ece" name="ECE (14-day)" dot={false} stroke={charts.series.warning} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </Panel>
  );
}

function DriftPanel() {
  const { data, loading, error, reload } = useApi(() => get("/quality/drift", { window_days: 30 }), []);
  if (loading) return <LoadingState label="Computing drift…" />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  return (
    <Panel
      title={`Drift: ${data.overall_status}`}
      subtitle={data.windows ? `Training split (n=${data.windows.reference.n}) vs last 30 days (n=${data.windows.current.n}) · PSI + KS` : undefined}
    >
      {!data.features?.length ? (
        <EmptyState label="No data to compare yet." />
      ) : (
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left label-caps">
              <th className="py-1">Feature</th>
              <th>Group</th>
              <th>PSI</th>
              <th>KS p</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-surface-border">
            {data.features.map((f) => (
              <tr key={f.feature} data-testid="drift-row">
                <td className="py-1 font-mono">{f.feature}</td>
                <td className="text-ink-muted">{f.group}</td>
                <td className="font-mono">{f.psi ?? "—"}</td>
                <td className="font-mono">{f.ks_p_value ?? "—"}</td>
                <td className={BAND_CLASS[f.psi_band] || "text-ink-muted"}>{f.drifted ? "DRIFT" : f.psi_band || f.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

function QualityPanel() {
  const { data, loading, error, reload } = useApi(() => get("/quality/summary"), []);
  if (loading) return <LoadingState label="Loading data-quality reports…" />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (!data.n_lots_reported) return <EmptyState label={data.note || "No data-quality reports yet."} />;
  return (
    <Panel title="Ingest data quality" subtitle={`${data.n_lots_reported} lots validated on ingest`}>
      <div className="flex gap-4 text-sm mb-3">
        {["PASS", "WARN", "FAIL"].map((status) => (
          <div key={status}>
            <div className="label-caps">{status}</div>
            <div className="font-mono text-ink-primary">{data.status_counts[status] || 0}</div>
          </div>
        ))}
      </div>
      <ul className="text-xs space-y-1">
        {Object.entries(data.issues_per_check || {}).map(([check, n]) => (
          <li key={check} className="flex justify-between">
            <span className="font-mono text-ink-secondary">{check}</span>
            <span className="font-mono">{n}</span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
