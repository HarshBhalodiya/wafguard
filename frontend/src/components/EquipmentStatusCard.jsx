import { Link } from "react-router-dom";

const STATUS_STYLES = {
  operational: { label: "Operational", color: "text-status-healthy", bar: "bg-status-healthy" },
  warning: { label: "Warning", color: "text-status-warning", bar: "bg-status-warning" },
  critical: { label: "Critical", color: "text-status-critical", bar: "bg-status-critical" },
  maintenance: { label: "Maintenance", color: "text-status-info", bar: "bg-status-info" },
};

export default function EquipmentStatusCard({ equipment }) {
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
        <div className="h-1.5 rounded-full bg-surface-raised overflow-hidden">
          <div className={`h-full rounded-full ${style.bar}`} style={{ width: `${equipment.health_score}%` }} />
        </div>
      </div>

      {equipment.anomaly_status && (
        <div className="mt-2 text-[11px] text-status-warning">⚠ {equipment.anomaly_status}</div>
      )}
    </Link>
  );
}
