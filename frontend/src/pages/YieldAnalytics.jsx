import { BarChart, Bar, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApi } from "../hooks/useApi";
import { get } from "../services/api";
import { LoadingState, ErrorState } from "../components/LoadingState.jsx";
import Panel from "../components/Panel.jsx";
import YieldTrendChart from "../components/YieldTrendChart.jsx";
import { charts } from "../theme.js";

function groupAverage(lots, key) {
  const groups = {};
  for (const lot of lots) {
    if (lot.yield === null || lot.yield === undefined) continue;
    const k = lot[key];
    if (!groups[k]) groups[k] = [];
    groups[k].push(lot.yield);
  }
  return Object.entries(groups)
    .map(([k, values]) => ({ name: k, avg_yield: Math.round((values.reduce((a, b) => a + b, 0) / values.length) * 10) / 10, n: values.length }))
    .sort((a, b) => a.avg_yield - b.avg_yield);
}

export default function YieldAnalytics() {
  const dashboardState = useApi(() => get("/dashboard"), []);
  const lotsState = useApi(() => get("/lots", { limit: 950 }), []);

  if (dashboardState.loading || lotsState.loading) return <LoadingState label="Loading yield analytics…" />;
  if (dashboardState.error) return <ErrorState error={dashboardState.error} onRetry={dashboardState.reload} />;
  if (lotsState.error) return <ErrorState error={lotsState.error} onRetry={lotsState.reload} />;

  const byProduct = groupAverage(lotsState.data.items, "product");
  const byEquipment = groupAverage(lotsState.data.items, "equipment");
  const yieldTrend = dashboardState.data.yield_trend.map((p) => ({ period: p.period.replace("2026-W", "W"), value: p.value }));

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-ink-primary">Yield Analytics</h1>
        <p className="text-sm text-ink-secondary">Yield trend and breakdowns by product and equipment.</p>
      </div>

      <Panel title="Yield Trend" subtitle="Weekly average, completed lots">
        <YieldTrendChart data={yieldTrend} />
      </Panel>

      <div className="grid lg:grid-cols-2 gap-4">
        <Panel title="Average Yield by Product">
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={byProduct} layout="vertical" margin={{ left: 20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={charts.grid} horizontal={false} />
              <XAxis type="number" domain={[0, 100]} tick={{ fill: charts.axis, fontSize: 11 }} axisLine={{ stroke: charts.grid }} tickLine={false} />
              <YAxis type="category" dataKey="name" tick={{ fill: charts.axis, fontSize: 11 }} axisLine={false} tickLine={false} width={80} />
              <Tooltip contentStyle={{ background: charts.tooltip.background, border: `1px solid ${charts.tooltip.border}`, borderRadius: 6, fontSize: 12 }} labelStyle={{ color: charts.tooltip.text }} formatter={(v) => [`${v}%`, "Avg yield"]} />
              <Bar dataKey="avg_yield" fill={charts.series.accent} radius={0} />
            </BarChart>
          </ResponsiveContainer>
        </Panel>
        <Panel title="Average Yield by Equipment" subtitle="Lowest first — worth investigating">
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={byEquipment.slice(0, 10)} layout="vertical" margin={{ left: 20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={charts.grid} horizontal={false} />
              <XAxis type="number" domain={[0, 100]} tick={{ fill: charts.axis, fontSize: 11 }} axisLine={{ stroke: charts.grid }} tickLine={false} />
              <YAxis type="category" dataKey="name" tick={{ fill: charts.axis, fontSize: 10 }} axisLine={false} tickLine={false} width={80} />
              <Tooltip contentStyle={{ background: charts.tooltip.background, border: `1px solid ${charts.tooltip.border}`, borderRadius: 6, fontSize: 12 }} labelStyle={{ color: charts.tooltip.text }} formatter={(v) => [`${v}%`, "Avg yield"]} />
              <Bar dataKey="avg_yield" fill={charts.series.warning} radius={0} />
            </BarChart>
          </ResponsiveContainer>
        </Panel>
      </div>
    </div>
  );
}
