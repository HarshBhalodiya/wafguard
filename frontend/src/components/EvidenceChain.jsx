import { EmptyState } from "./LoadingState.jsx";
import { formatDate } from "../utils/format";

const KIND_STYLES = {
  FACT: "text-status-info bg-status-info/10 border-status-info/30",
  MODEL_OUTPUT: "text-brand bg-brand/10 border-brand/30",
  INFERENCE: "text-status-warning bg-status-warning/10 border-status-warning/30",
  RECOMMENDATION: "text-status-healthy bg-status-healthy/10 border-status-healthy/30",
};

/**
 * The evidence-chain visualization (spec section 34): a step-by-step trail
 * from equipment/parameter facts through model output to a recommendation,
 * each step tagged with what KIND of claim it is so an engineer can tell
 * "measured fact" from "model inference" at a glance.
 */
export default function EvidenceChain({ steps }) {
  if (!steps || steps.length === 0) return <EmptyState label="No evidence chain available yet." />;

  return (
    <ol className="border-l-2 border-surface-border ml-1.5">
      {steps.map((step, idx) => (
        <li key={idx} className="relative pl-6 pb-6 last:pb-0">
          <span className="absolute -left-[9px] top-0.5 w-4 h-4 rounded-full bg-surface-panel border-2 border-brand" />
          <div className="flex flex-wrap items-center gap-2">
            <span className={`text-[10px] font-semibold uppercase tracking-wide rounded border px-1.5 py-0.5 ${KIND_STYLES[step.kind] || KIND_STYLES.FACT}`}>
              {step.kind?.replace("_", " ") || "FACT"}
            </span>
            {step.source && <span className="text-[11px] text-ink-muted font-mono">{step.source}</span>}
            {step.timestamp && <span className="text-[11px] text-ink-muted">{formatDate(step.timestamp)}</span>}
          </div>
          <p className="text-sm text-ink-primary mt-1.5">{step.label}</p>
          {step.value !== undefined && step.value !== null && (
            <div className="text-xs text-ink-secondary mt-1 tabular-nums">confidence/value: {step.value}</div>
          )}
        </li>
      ))}
    </ol>
  );
}
