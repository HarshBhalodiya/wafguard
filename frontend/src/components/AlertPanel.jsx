import { Link } from "react-router-dom";
import { EmptyState } from "./LoadingState.jsx";
import { formatDate } from "../utils/format";

const SEVERITY_DOT = {
  HIGH: "bg-status-critical",
  MEDIUM: "bg-status-warning",
  LOW: "bg-status-info",
};

export default function AlertPanel({ alerts }) {
  if (!alerts || alerts.length === 0) return <EmptyState label="No active alerts." />;

  return (
    <ul className="divide-y divide-surface-border">
      {alerts.map((alert) => (
        <li key={alert.alert_id || alert._id || `${alert.lot_id}-${alert.created_at}`} className="py-2.5 flex items-start gap-2.5">
          <span className={`w-1.5 h-1.5 rounded-full mt-1.5 shrink-0 ${SEVERITY_DOT[alert.severity] || SEVERITY_DOT.LOW}`} />
          <div className="min-w-0 flex-1">
            <div className="text-sm text-ink-primary truncate">{alert.message}</div>
            <div className="text-[11px] text-ink-muted mt-0.5 flex items-center gap-2">
              <span>{formatDate(alert.created_at)}</span>
              {alert.lot_id && (
                <Link to={`/lots/${alert.lot_id}`} className="text-brand hover:underline">
                  {alert.lot_id}
                </Link>
              )}
              {/* P8-3: triage status chip, only when the payload carries one */}
              {alert.status && (
                <span className="text-[10px] px-1.5 py-0.5 rounded border uppercase tracking-wide text-status-warning border-status-warning/40">
                  {alert.status}
                </span>
              )}
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}
