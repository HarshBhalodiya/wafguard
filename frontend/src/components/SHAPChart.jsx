import RankedBarChart from "./RankedBarChart.jsx";
import { charts } from "../theme.js";
import { titleCase } from "../utils/format";

/** SHAP contributions — colored by direction, since "increases risk" vs "decreases risk" is the point. */
export default function SHAPChart({ contributions }) {
  const items = (contributions || []).map((c) => ({
    label: titleCase(c.feature),
    value: c.relative_contribution,
    color: c.direction === "increases_risk" ? charts.series.negative : charts.series.positive,
  }));
  return <RankedBarChart items={items} emptyLabel="No SHAP explanation available yet." />;
}
