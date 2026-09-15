import { Link } from "react-router-dom";
import RiskBadge from "./RiskBadge.jsx";
import { EmptyState } from "./LoadingState.jsx";
import { formatShortDate, formatYield } from "../utils/format";

export default function LotRiskTable({ lots, compact = false }) {
  if (!lots || lots.length === 0) return <EmptyState label="No lots to show." />;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-ink-muted border-b border-surface-border">
            <th className="py-2 pr-4 font-medium">Lot</th>
            <th className="py-2 pr-4 font-medium">Product</th>
            {!compact && <th className="py-2 pr-4 font-medium">Equipment</th>}
            {!compact && <th className="py-2 pr-4 font-medium">Step</th>}
            <th className="py-2 pr-4 font-medium">Yield</th>
            <th className="py-2 pr-4 font-medium">Risk</th>
            {!compact && <th className="py-2 pr-4 font-medium">Started</th>}
          </tr>
        </thead>
        <tbody>
          {lots.map((lot) => (
            <tr key={lot.lot_id} className="border-b border-surface-border/60 hover:bg-surface-raised/60 transition-colors">
              <td className="py-2.5 pr-4">
                <Link to={`/lots/${lot.lot_id}`} className="text-brand hover:underline font-medium font-mono text-xs">
                  {lot.lot_id}
                </Link>
              </td>
              <td className="py-2.5 pr-4 text-ink-secondary">{lot.product}</td>
              {!compact && <td className="py-2.5 pr-4 text-ink-secondary font-mono text-xs">{lot.equipment}</td>}
              {!compact && <td className="py-2.5 pr-4 text-ink-secondary">{lot.process_step}</td>}
              <td className="py-2.5 pr-4 tabular-nums text-ink-primary">
                {lot.predicted_yield !== null && lot.predicted_yield !== undefined
                  ? formatYield(lot.predicted_yield)
                  : formatYield(lot.yield)}
              </td>
              <td className="py-2.5 pr-4">
                <RiskBadge level={lot.risk_level} probability={lot.risk_probability} size="sm" />
              </td>
              {!compact && <td className="py-2.5 pr-4 text-ink-muted text-xs">{formatShortDate(lot.process_start)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
