import RankedBarChart from "./RankedBarChart.jsx";

export default function RootCauseChart({ candidates }) {
  const items = (candidates || []).map((c) => ({ label: c.candidate, value: c.score }));
  return <RankedBarChart items={items} barColor="#22d3ee" emptyLabel="No root-cause candidates yet." />;
}
