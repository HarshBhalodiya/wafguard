import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { charts } from "../theme.js";
import { EmptyState } from "./LoadingState.jsx";

export default function YieldTrendChart({ data }) {
  if (!data || data.length === 0) return <EmptyState label="No yield trend data yet." />;

  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={charts.grid} />
        <XAxis dataKey="period" tick={{ fill: charts.axis, fontSize: 11 }} axisLine={{ stroke: charts.grid }} tickLine={false} />
        <YAxis
          domain={["dataMin - 3", 100]}
          tick={{ fill: charts.axis, fontSize: 11 }}
          axisLine={{ stroke: charts.grid }}
          tickLine={false}
          width={36}
        />
        <Tooltip
          contentStyle={{ background: charts.tooltip.background, border: `1px solid ${charts.tooltip.border}`, borderRadius: 6, fontSize: 12 }}
          labelStyle={{ color: charts.tooltip.text }}
          formatter={(value) => [`${value}%`, "Avg yield"]}
        />
        <Line type="monotone" dataKey="value" stroke={charts.series.accent} strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
      </LineChart>
    </ResponsiveContainer>
  );
}
