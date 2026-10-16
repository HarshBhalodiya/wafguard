import { Link } from "react-router-dom";
import { WarningIcon } from "./icons.jsx";

const STATUS_STYLES = {
  operational: { label: "Operational", color: "text-status-healthy", bar: "bg-status-healthy" },
  warning: { label: "Warning", color: "text-status-warning", bar: "bg-status-warning" },
  critical: { label: "Critical", color: "text-status-critical", bar: "bg-status-critical" },
  maintenance: { label: "Maintenance", color: "text-status-info", bar: "bg-status-info" },
};

export default function EquipmentStatusCard({ equipment, spc }) {
  const style = STATUS_STYLES[equipment.status] || STATUS_STYLES.operational;
  return (
    <Link to={`/equipment`} className="panel p-4 block hover:border-brand/40 transition-colors">
      <div className="flex items-center justify-between">
        <div className="font-mono text-sm font-semibold text-ink-primary">{equipment.equipment_id}</div>
        <span className={`text-xs font-medium ${style.color}`}>{style.label}</span>
      </div>
      <div className="text-xs text-ink-muted mt-0.5">{equipment.tool_family}{equipment.chamber ? ` · ${equipment.chamber}` : ""}</div>

      <div className="mt-3">
        <div className="flex items-center justify-between text-[11px] text-ink-muted mb-1">
          <span>Health score</span>
          <span className="tabular-nums">{Math.round(equipment.health_score)}/100</span>
        </div>
        <div className="h-1.5 rounded-sm bg-surface-raised overflow-hidden">
          <div className={`h-full rounded-sm ${style.bar}`} style={{ width: `${equipment.health_score}%` }} />
        </div>
      </div>

      {equipment.anomaly_status && (
        <div className="mt-2 flex items-center gap-1.5 text-[11px] text-status-warning">
          <WarningIcon />
          <span>{equipment.anomaly_status}</span>
        </div>
      )}

      {/* P8-4: control-chart breach context from the P5-2 API. Honest label:
          lot-level SPC (running mean ±3σ vs the tool's own prior history), never
          per-reading SPC, and the static generator health_score above is not
          reinterpreted by it. Omitted entirely when the chart data is absent. */}
      {spc && (
        <div className="mt-2 text-[11px] flex items-center gap-1.5">
          <span className="text-ink-muted">SPC (lot-level):</span>
          <span className={spc.breaches > 0 ? "text-status-critical" : "text-status-healthy"}>
            {spc.breaches} breach{spc.breaches === 1 ? "" : "es"}
          </span>
          <span className="text-ink-muted">· last {spc.window} lots</span>
        </div>
      )}
    </Link>
  );
}
