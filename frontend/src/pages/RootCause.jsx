import { useSearchParams, Link } from "react-router-dom";
import { useApi } from "../hooks/useApi";
import { get } from "../services/api";
import { LoadingState, ErrorState } from "../components/LoadingState.jsx";
import Panel from "../components/Panel.jsx";
import LotPicker from "../components/LotPicker.jsx";
import RootCauseChart from "../components/RootCauseChart.jsx";
import EvidenceChain from "../components/EvidenceChain.jsx";

export default function RootCause() {
  const [params] = useSearchParams();
  const lotId = params.get("lot_id");

  const { data, loading, error, reload } = useApi(() => (lotId ? get(`/lots/${lotId}/root-cause`) : Promise.resolve(null)), [lotId]);

  if (!lotId) return <LotPicker title="Root Cause Analysis" basePath="/root-cause" />;
  if (loading) return <LoadingState label="Computing root-cause candidates…" />;
  if (error) return <ErrorState error={error} onRetry={reload} />;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-ink-primary">
          Root Cause — <Link to={`/lots/${lotId}`} className="text-brand hover:underline font-mono">{lotId}</Link>
        </h1>
        <p className="text-sm text-ink-secondary">
          Ranked candidates from SHAP attribution, parameter deviation, historical occurrence, equipment association, wafer pattern, and
          temporal evidence. These are <span className="text-ink-primary font-medium">candidates</span>, not confirmed causes.
        </p>
      </div>

      <Panel title="Ranked Candidates">
        <RootCauseChart candidates={data.candidates} />
      </Panel>

      {data.candidates.map((candidate) => (
        <Panel key={candidate.candidate} title={`${candidate.candidate} — ${candidate.confidence} confidence`} subtitle={`Score ${candidate.score.toFixed(3)}`}>
          <EvidenceChain steps={candidate.evidence} />
        </Panel>
      ))}
    </div>
  );
}
