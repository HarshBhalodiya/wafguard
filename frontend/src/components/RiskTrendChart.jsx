import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { EmptyState } from "./LoadingState.jsx";

export default function RiskTrendChart({ data }) {
  if (!data || data.length === 0) return <EmptyState label="No risk trend data yet." />;

  return (
    <ResponsiveContainer width="100%" height={240}>
      <AreaChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
        <defs>
          <linearGradient id="riskFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.4} />
            <stop offset="95%" stopColor="#f59e0b" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#232b36" />
        <XAxis dataKey="period" tick={{ fill: "#8b96a5", fontSize: 11 }} axisLine={{ stroke: "#232b36" }} tickLine={false} />
        <YAxis
          domain={[0, 1]}
          tickFormatter={(v) => `${Math.round(v * 100)}%`}
          tick={{ fill: "#8b96a5", fontSize: 11 }}
          axisLine={{ stroke: "#232b36" }}
          tickLine={false}
          width={36}
        />
        <Tooltip
          contentStyle={{ background: "#121820", border: "1px solid #232b36", borderRadius: 8, fontSize: 12 }}
          labelStyle={{ color: "#e6edf3" }}
          formatter={(value) => [`${Math.round(value * 100)}%`, "Avg risk"]}
        />
        <Area type="monotone" dataKey="value" stroke="#f59e0b" strokeWidth={2} fill="url(#riskFill)" />
      </AreaChart>
    </ResponsiveContainer>
  );
}
