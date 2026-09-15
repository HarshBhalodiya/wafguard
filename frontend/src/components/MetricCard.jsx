export default function MetricCard({ label, value, delta, deltaGood = "up", sublabel, tone = "default" }) {
  const toneClasses = {
    default: "text-ink-primary",
    critical: "text-status-critical",
    warning: "text-status-warning",
    healthy: "text-status-healthy",
  };

  const deltaIsPositive = typeof delta === "number" ? delta > 0 : null;
  const deltaGoodDirection = deltaGood === "up" ? deltaIsPositive : deltaIsPositive === false;
  const deltaColor = deltaIsPositive === null ? "text-ink-muted" : deltaGoodDirection ? "text-status-healthy" : "text-status-critical";

  return (
    <div className="panel p-4">
      <div className="label-caps">{label}</div>
      <div className={`mt-2 text-2xl font-semibold tabular-nums ${toneClasses[tone]}`}>{value}</div>
      <div className="mt-1 flex items-center gap-2 text-xs">
        {delta !== undefined && delta !== null && (
          <span className={`font-medium ${deltaColor}`}>
            {deltaIsPositive ? "▲" : "▼"} {Math.abs(delta)}
          </span>
        )}
        {sublabel && <span className="text-ink-muted">{sublabel}</span>}
      </div>
    </div>
  );
}
