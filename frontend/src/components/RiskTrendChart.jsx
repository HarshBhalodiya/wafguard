import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { charts } from "../theme.js";
import { EmptyState } from "./LoadingState.jsx";

export default function RiskTrendChart({ data }) {
  if (!data || data.length === 0) return <EmptyState label="No risk trend data yet." />;

  return (
    <ResponsiveContainer width="100%" height={240}>
      <AreaChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={charts.grid} />
        <XAxis dataKey="period" tick={{ fill: charts.axis, fontSize: 11 }} axisLine={{ stroke: charts.grid }} tickLine={false} />
        <YAxis
          domain={[0, 1]}
          tickFormatter={(v) => `${Math.round(v * 100)}%`}
          tick={{ fill: charts.axis, fontSize: 11 }}
          axisLine={{ stroke: charts.grid }}
          tickLine={false}
          width={36}
        />
        <Tooltip
          contentStyle={{ background: charts.tooltip.background, border: `1px solid ${charts.tooltip.border}`, borderRadius: 6, fontSize: 12 }}
          labelStyle={{ color: charts.tooltip.text }}
          formatter={(value) => [`${Math.round(value * 100)}%`, "Avg risk"]}
        />
        {/* Flat translucent fill — the trend reads from the line, not a gradient. */}
        <Area
          type="monotone"
          dataKey="value"
          stroke={charts.series.warning}
          strokeWidth={2}
          fill={charts.series.warning}
          fillOpacity={charts.fillOpacity}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
