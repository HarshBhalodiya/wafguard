import RankedBarChart from "./RankedBarChart.jsx";
import { titleCase } from "../utils/format";

/** SHAP contributions — colored by direction, since "increases risk" vs "decreases risk" is the point. */
export default function SHAPChart({ contributions }) {
  const items = (contributions || []).map((c) => ({
    label: titleCase(c.feature),
    value: c.relative_contribution,
    color: c.direction === "increases_risk" ? "#ef4444" : "#22c55e",
  }));
  return <RankedBarChart items={items} emptyLabel="No SHAP explanation available yet." />;
}
