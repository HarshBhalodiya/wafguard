import { formatDate } from "../utils/format";

function MetricRow({ label, baseline, production }) {
  return (
    <tr className="border-b border-surface-border/60">
      <td className="py-1.5 pr-4 text-ink-secondary">{label}</td>
      <td className="py-1.5 pr-4 text-right tabular-nums text-ink-primary">{baseline}</td>
      <td className="py-1.5 text-right tabular-nums text-brand font-medium">{production}</td>
    </tr>
  );
}

const fmt = (v, digits = 3) => (v === null || v === undefined ? "—" : Number(v).toFixed(digits));

export default function ModelPerformanceCard({ model }) {
  const { model_name, version, task, dataset, trained_at, metrics } = model;
  const isVision = task === "vision_classification";

  return (
    <div className="panel p-4">
      <div className="flex items-start justify-between">
        <div>
          <h3 className="text-sm font-semibold text-ink-primary font-mono">{model_name}</h3>
          <p className="text-xs text-ink-muted mt-0.5">{dataset}</p>
        </div>
        <span className="text-[11px] text-ink-muted">v{version}</span>
      </div>
      <p className="text-[11px] text-ink-muted mt-1">Trained {formatDate(trained_at)}</p>

      {isVision ? (
        <div className="mt-3">
          <div className="flex gap-4 text-sm mb-2">
            <div>
              <span className="text-ink-muted">Accuracy </span>
              <span className="text-ink-primary font-medium tabular-nums">{fmt(metrics.accuracy)}</span>
            </div>
            <div>
              <span className="text-ink-muted">Macro-F1 </span>
              <span className="text-ink-primary font-medium tabular-nums">{fmt(metrics.macro_f1)}</span>
            </div>
          </div>
          <div className="max-h-40 overflow-y-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-ink-muted text-left">
                  <th className="py-1 font-medium">Class</th>
                  <th className="py-1 font-medium text-right">Precision</th>
                  <th className="py-1 font-medium text-right">Recall</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(metrics.per_class || {}).map(([cls, stats]) => (
                  <tr key={cls} className="border-t border-surface-border/60">
                    <td className="py-1 text-ink-secondary">{cls}</td>
                    <td className="py-1 text-right tabular-nums text-ink-primary">{fmt(stats.precision, 2)}</td>
                    <td className="py-1 text-right tabular-nums text-ink-primary">{fmt(stats.recall, 2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <table className="w-full text-xs mt-3">
          <thead>
            <tr className="text-ink-muted text-left border-b border-surface-border">
              <th className="py-1.5 font-medium">Metric</th>
              <th className="py-1.5 font-medium text-right">Baseline (RF)</th>
              <th className="py-1.5 font-medium text-right">Production (XGB)</th>
            </tr>
          </thead>
          <tbody>
            {task === "regression" ? (
              <>
                <MetricRow label="MAE" baseline={fmt(metrics.baseline?.mae)} production={fmt(metrics.production?.mae)} />
                <MetricRow label="RMSE" baseline={fmt(metrics.baseline?.rmse)} production={fmt(metrics.production?.rmse)} />
                <MetricRow label="R²" baseline={fmt(metrics.baseline?.r2)} production={fmt(metrics.production?.r2)} />
              </>
            ) : (
              <>
                <MetricRow label="Precision" baseline={fmt(metrics.baseline?.precision)} production={fmt(metrics.production?.precision)} />
                <MetricRow label="Recall" baseline={fmt(metrics.baseline?.recall)} production={fmt(metrics.production?.recall)} />
                <MetricRow label="F1" baseline={fmt(metrics.baseline?.f1)} production={fmt(metrics.production?.f1)} />
                <MetricRow label="ROC-AUC" baseline={fmt(metrics.baseline?.roc_auc)} production={fmt(metrics.production?.roc_auc)} />
                <MetricRow label="PR-AUC" baseline={fmt(metrics.baseline?.pr_auc)} production={fmt(metrics.production?.pr_auc)} />
              </>
            )}
          </tbody>
        </table>
      )}
      {!isVision && metrics.production?.class_balance && (
        <p className="text-[11px] text-ink-muted mt-2">
          Class balance: {metrics.production.class_balance.positive} positive / {metrics.production.class_balance.negative} negative (
          {(metrics.production.class_balance.positive_rate * 100).toFixed(1)}%)
        </p>
      )}
    </div>
  );
}
