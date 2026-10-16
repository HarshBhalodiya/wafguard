import RankedBarChart from "./RankedBarChart.jsx";
import { charts } from "../theme.js";

export default function RootCauseChart({ candidates }) {
  const items = (candidates || []).map((c) => ({ label: c.candidate, value: c.score }));
  return <RankedBarChart items={items} barColor={charts.series.accent} emptyLabel="No root-cause candidates yet." />;
}
