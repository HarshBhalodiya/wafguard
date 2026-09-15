export default function RecommendationCard({ recommendation }) {
  if (!recommendation) return null;
  const { evidence, prediction, recommendation_type, recommendations } = recommendation;

  return (
    <div className="space-y-4">
      <div className="grid sm:grid-cols-2 gap-3">
        <div className="rounded-lg border border-status-info/30 bg-status-info/5 p-3">
          <div className="label-caps text-status-info mb-1">Detected</div>
          <p className="text-sm text-ink-primary">{evidence}</p>
        </div>
        <div className="rounded-lg border border-brand/30 bg-brand/5 p-3">
          <div className="label-caps text-brand mb-1">Prediction</div>
          <p className="text-sm text-ink-primary">{prediction}</p>
        </div>
      </div>

      <div>
        <div className="flex items-center gap-2 mb-2">
          <span className="label-caps text-status-healthy">{recommendation_type}</span>
        </div>
        <ol className="space-y-1.5">
          {recommendations.map((step, idx) => (
            <li key={idx} className="text-sm text-ink-primary flex gap-2">
              <span className="text-ink-muted shrink-0">{idx + 1}.</span>
              <span>{step.replace(/^\d+\.\s*/, "")}</span>
            </li>
          ))}
        </ol>
      </div>
      <p className="text-[11px] text-ink-muted italic">
        These are suggested engineering investigations, not automatic process changes.
      </p>
    </div>
  );
}
