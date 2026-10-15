import { Link } from "react-router-dom";
import { useApi } from "../hooks/useApi";
import { get } from "../services/api";
import { LoadingState, ErrorState } from "../components/LoadingState.jsx";
import Panel from "../components/Panel.jsx";
import MetricCard from "../components/MetricCard.jsx";
import YieldTrendChart from "../components/YieldTrendChart.jsx";
import RootCauseChart from "../components/RootCauseChart.jsx";
import LotRiskTable from "../components/LotRiskTable.jsx";
import AlertPanel from "../components/AlertPanel.jsx";
import { formatYield } from "../utils/format";

export default function Dashboard() {
  const { data, loading, error, reload } = useApi(() => get("/dashboard"), []);

  if (loading) return <LoadingState label="Loading dashboard…" />;
  if (error) return <ErrorState error={error} onRetry={reload} />;

  const yieldTrend = data.yield_trend.map((p) => ({ period: p.period.replace("2026-W", "W"), value: p.value }));

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold text-ink-primary">Dashboard</h1>
        <p className="text-sm text-ink-secondary">What is happening in the fab right now?</p>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard label="Overall Yield" value={formatYield(data.overall_yield)} sublabel={`${data.total_lots} lots`} />
        <MetricCard
          label="At-Risk Lots"
          value={data.at_risk_lot_count}
          tone={data.at_risk_lot_count > 0 ? "warning" : "default"}
          sublabel={`${data.high_risk_lot_count} high risk`}
        />
        <MetricCard label="Defect Rate" value={`${(data.defect_rate * 100).toFixed(1)}%`} sublabel="wafers failed" />
        <MetricCard
          label="Equipment Warnings"
          value={data.equipment_warnings}
          tone={data.equipment_warnings > 0 ? "critical" : "healthy"}
          sublabel="tools needing attention"
        />
      </div>

      <div className="grid lg:grid-cols-2 gap-4">
        <Panel title="Yield Trend" subtitle="Weekly average, completed lots">
          <YieldTrendChart data={yieldTrend} />
        </Panel>
        <Panel title="At-Risk Lots" subtitle="Highest predicted risk right now">
          <LotRiskTable lots={data.top_risk_lots} compact />
        </Panel>
      </div>

      <div className="grid lg:grid-cols-2 gap-4">
        <Panel title="Top Root Causes" subtitle="Across current at-risk lots">
          <RootCauseChart candidates={data.top_root_causes.map((c) => ({ candidate: c.candidate, score: c.share }))} />
        </Panel>
        <Panel
          title="Recent Alerts"
          action={<Link to="/alerts" className="text-xs text-brand hover:underline">View all &amp; triage →</Link>}
        >
          <AlertPanel alerts={data.recent_alerts} />
        </Panel>
      </div>
    </div>
  );
}
