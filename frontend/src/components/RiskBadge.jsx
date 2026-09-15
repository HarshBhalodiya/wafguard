import { RISK_COLORS } from "../utils/format";

export default function RiskBadge({ level, probability, size = "md" }) {
  const colors = RISK_COLORS[level] || RISK_COLORS.LOW;
  const padding = size === "sm" ? "px-1.5 py-0.5 text-[11px]" : "px-2 py-1 text-xs";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md border font-semibold ${padding} ${colors.text} ${colors.bg} ${colors.border}`}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${colors.dot}`} />
      {level || "—"}
      {probability !== undefined && probability !== null && (
        <span className="opacity-75 font-normal">{Math.round(probability * 100)}%</span>
      )}
    </span>
  );
}
