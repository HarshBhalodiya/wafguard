import { useApi } from "../hooks/useApi";
import { get } from "../services/api";
import { LoadingState, ErrorState } from "../components/LoadingState.jsx";
import ModelPerformanceCard from "../components/ModelPerformanceCard.jsx";

export default function ModelPerformance() {
  const { data, loading, error, reload } = useApi(() => get("/model-performance"), []);

  if (loading) return <LoadingState label="Loading model performance…" />;
  if (error) return <ErrorState error={error} onRetry={reload} />;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-ink-primary">Model Performance</h1>
        <p className="text-sm text-ink-secondary">Measured metrics for every trained model — baseline vs. production, nothing assumed.</p>
      </div>
      <div className="grid lg:grid-cols-2 gap-4">
        {data.models.map((model) => (
          <ModelPerformanceCard key={`${model.model_name}-${model.version}`} model={model} />
        ))}
      </div>
    </div>
  );
}
