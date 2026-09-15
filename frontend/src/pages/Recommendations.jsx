import { useSearchParams, Link } from "react-router-dom";
import { useApi } from "../hooks/useApi";
import { get } from "../services/api";
import { LoadingState, ErrorState } from "../components/LoadingState.jsx";
import Panel from "../components/Panel.jsx";
import LotPicker from "../components/LotPicker.jsx";
import RecommendationCard from "../components/RecommendationCard.jsx";

export default function Recommendations() {
  const [params] = useSearchParams();
  const lotId = params.get("lot_id");

  const { data, loading, error, reload } = useApi(() => (lotId ? get(`/lots/${lotId}/recommendations`) : Promise.resolve(null)), [lotId]);

  if (!lotId) return <LotPicker title="Recommendations" basePath="/recommendations" />;
  if (loading) return <LoadingState label="Generating recommendations…" />;
  if (error) return <ErrorState error={error} onRetry={reload} />;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-ink-primary">
          Recommendations — <Link to={`/lots/${lotId}`} className="text-brand hover:underline font-mono">{lotId}</Link>
        </h1>
        <p className="text-sm text-ink-secondary">Evidence-grounded suggested engineering investigations.</p>
      </div>
      <Panel>
        <RecommendationCard recommendation={data} />
      </Panel>
    </div>
  );
}
