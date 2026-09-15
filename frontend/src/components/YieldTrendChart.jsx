import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { EmptyState } from "./LoadingState.jsx";

export default function YieldTrendChart({ data }) {
  if (!data || data.length === 0) return <EmptyState label="No yield trend data yet." />;

  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#232b36" />
        <XAxis dataKey="period" tick={{ fill: "#8b96a5", fontSize: 11 }} axisLine={{ stroke: "#232b36" }} tickLine={false} />
        <YAxis
          domain={["dataMin - 3", 100]}
          tick={{ fill: "#8b96a5", fontSize: 11 }}
          axisLine={{ stroke: "#232b36" }}
          tickLine={false}
          width={36}
        />
        <Tooltip
          contentStyle={{ background: "#121820", border: "1px solid #232b36", borderRadius: 8, fontSize: 12 }}
          labelStyle={{ color: "#e6edf3" }}
          formatter={(value) => [`${value}%`, "Avg yield"]}
        />
        <Line type="monotone" dataKey="value" stroke="#22d3ee" strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
      </LineChart>
    </ResponsiveContainer>
  );
}
