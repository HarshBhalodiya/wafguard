const LABELS = {
  secom: { text: "Real UCI SECOM data", tone: "info" },
  wm811k: { text: "Real WM-811K data", tone: "info" },
  wm811k_synthetic: { text: "DEMO / SIMULATED DATA", tone: "warning" },
  synthetic: { text: "DEMO / SIMULATED DATA", tone: "warning" },
  imported: { text: "Imported data", tone: "info" },
};

const TONE_CLASSES = {
  info: "text-status-info bg-status-info/10 border-status-info/30",
  warning: "text-status-warning bg-status-warning/10 border-status-warning/30",
};

/** Spec-mandated transparency label — never let a synthetic number pass as real fab data. */
export default function DataProvenanceBadge({ source, className = "" }) {
  const entry = LABELS[source] || { text: "Unknown source", tone: "warning" };
  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${TONE_CLASSES[entry.tone]} ${className}`}
      title={entry.text}
    >
      {entry.text}
    </span>
  );
}
