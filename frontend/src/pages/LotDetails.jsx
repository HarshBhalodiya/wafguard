import { useParams, Link } from "react-router-dom";
import { useApi } from "../hooks/useApi";
import { get } from "../services/api";
import { LoadingState, ErrorState } from "../components/LoadingState.jsx";
import Panel from "../components/Panel.jsx";
import RiskBadge from "../components/RiskBadge.jsx";
import DataProvenanceBadge from "../components/DataProvenanceBadge.jsx";
import RootCauseChart from "../components/RootCauseChart.jsx";
import EvidenceChain from "../components/EvidenceChain.jsx";
import RecommendationCard from "../components/RecommendationCard.jsx";
import { formatDate, formatYield, titleCase } from "../utils/format";

const PARAM_LABELS = {
  temperature: { label: "Temperature", unit: "°C" },
  pressure: { label: "Pressure", unit: "Torr" },
  rf_power: { label: "RF Power", unit: "W" },
  gas_flow: { label: "Gas Flow", unit: "sccm" },
  etch_time: { label: "Etch Time", unit: "sec" },
  voltage: { label: "Voltage", unit: "V" },
  current: { label: "Current", unit: "A" },
};

function buildEvidenceChainSteps(lot, rootCause, recommendation) {
  const steps = [];
  if (lot) {
    steps.push({
      kind: "FACT",
      label: `Lot ${lot.lot_id} ran on ${lot.equipment} (${lot.process_step}).`,
      source: "lots",
      timestamp: lot.process_start,
    });
  }
  const top = rootCause?.candidates?.[0];
  if (top) {
    for (const ev of top.evidence.slice(0, 4)) {
      steps.push(ev);
    }
    steps.push({
      kind: "MODEL_OUTPUT",
      label: `Root-cause candidate: ${top.candidate} (${top.confidence} confidence, score ${top.score.toFixed(2)}).`,
      source: "rootcause_multi_signal_v1",
    });
  }
  if (lot) {
    steps.push({
      kind: "MODEL_OUTPUT",
      label: `Predicted yield ${formatYield(lot.predicted_yield)}, risk ${Math.round((lot.risk_probability || 0) * 100)}% (${lot.risk_level}).`,
      source: "lot_yield_classifier",
    });
  }
  if (recommendation?.recommendations?.[0]) {
    steps.push({
      kind: "RECOMMENDATION",
      label: recommendation.recommendations[0].replace(/^\d+\.\s*/, ""),
      source: "recommendation engine",
    });
  }
  return steps;
}

export default function LotDetails() {
  const { lotId } = useParams();
  const lotState = useApi(() => get(`/lots/${lotId}`), [lotId]);
  const rootCauseState = useApi(() => get(`/lots/${lotId}/root-cause`), [lotId]);
  const recommendationState = useApi(() => get(`/lots/${lotId}/recommendations`), [lotId]);
  const wafersState = useApi(() => get("/wafers", { lot_id: lotId }), [lotId]);

  if (lotState.loading) return <LoadingState label={`Loading ${lotId}…`} />;
  if (lotState.error) return <ErrorState error={lotState.error} onRetry={lotState.reload} />;

  const lot = lotState.data;
  const params = lot.process_parameters || {};

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-lg font-semibold text-ink-primary font-mono">{lot.lot_id}</h1>
            <RiskBadge level={lot.risk_level} probability={lot.risk_probability} />
            <DataProvenanceBadge source={lot.data_source} />
          </div>
          <p className="text-sm text-ink-secondary mt-1">
            {lot.product} · {lot.node} · {lot.wafer_count} wafers · {lot.equipment} ({lot.process_step}) · started {formatDate(lot.process_start)}
          </p>
        </div>
        <div className="flex gap-2 text-sm">
          <Link to={`/similar-lots?lot_id=${lot.lot_id}`} className="px-3 py-1.5 rounded-md border border-surface-border text-ink-secondary hover:text-ink-primary hover:border-brand/40">
            Similar Lots
          </Link>
          <Link to={`/what-if?lot_id=${lot.lot_id}`} className="px-3 py-1.5 rounded-md border border-brand/40 text-brand hover:bg-brand/10">
            What-If Simulator
          </Link>
        </div>
      </div>

      <div className="grid lg:grid-cols-3 gap-4">
        <Panel title="Predicted Yield">
          <div className="text-3xl font-semibold text-ink-primary tabular-nums">{formatYield(lot.predicted_yield ?? lot.yield)}</div>
          {lot.yield !== null && lot.predicted_yield !== null && (
            <div className="text-xs text-ink-muted mt-1">Actual (if known): {formatYield(lot.yield)}</div>
          )}
        </Panel>
        <Panel title="Risk Probability">
          <div className="text-3xl font-semibold text-ink-primary tabular-nums">{Math.round((lot.risk_probability || 0) * 100)}%</div>
          <div className="mt-1"><RiskBadge level={lot.risk_level} /></div>
        </Panel>
        <Panel title="Status">
          <div className="text-3xl font-semibold text-ink-primary">{titleCase(lot.status)}</div>
          {lot.is_demo_incident && <div className="text-xs text-status-warning mt-1">Deterministic demo incident lot</div>}
        </Panel>
      </div>

      <Panel title="Process Parameters">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          {Object.entries(PARAM_LABELS).map(([key, { label, unit }]) => (
            <div key={key}>
              <div className="label-caps">{label}</div>
              <div className="text-lg font-medium text-ink-primary tabular-nums mt-1">
                {params[key] !== undefined ? params[key].toFixed(2) : "—"}
                <span className="text-xs text-ink-muted ml-1">{unit}</span>
              </div>
            </div>
          ))}
        </div>
      </Panel>

      <div className="grid lg:grid-cols-2 gap-4">
        <Panel title="Root Cause" subtitle="Ranked candidates — not confirmed causes" action={<Link to={`/root-cause?lot_id=${lot.lot_id}`} className="text-xs text-brand hover:underline">Details →</Link>}>
          {rootCauseState.loading && <LoadingState label="Computing root cause…" />}
          {rootCauseState.error && <ErrorState error={rootCauseState.error} onRetry={rootCauseState.reload} />}
          {rootCauseState.data && <RootCauseChart candidates={rootCauseState.data.candidates} />}
        </Panel>
        <Panel title="Wafers" subtitle={`${wafersState.data?.length || 0} wafers in this lot`} action={<Link to={`/wafer-map?lot_id=${lot.lot_id}`} className="text-xs text-brand hover:underline">View wafer maps →</Link>}>
          {wafersState.loading && <LoadingState label="Loading wafers…" />}
          {wafersState.data && (
            <div className="grid grid-cols-5 gap-1.5">
              {wafersState.data.slice(0, 25).map((w) => (
                <Link
                  key={w.wafer_id}
                  to={`/wafer-map?lot_id=${lot.lot_id}&wafer_id=${w.wafer_id}`}
                  className={`aspect-square rounded flex items-center justify-center text-[10px] font-mono ${
                    w.yield_status === "fail" ? "bg-status-critical/20 text-status-critical" : "bg-status-healthy/10 text-status-healthy"
                  }`}
                  title={w.wafer_id}
                >
                  {w.slot}
                </Link>
              ))}
            </div>
          )}
        </Panel>
      </div>

      <Panel title="Evidence Chain" subtitle="Equipment → parameter → model → recommendation, each step labeled by claim type">
        <EvidenceChain steps={buildEvidenceChainSteps(lot, rootCauseState.data, recommendationState.data)} />
      </Panel>

      <Panel title="Recommendations" action={<Link to={`/recommendations?lot_id=${lot.lot_id}`} className="text-xs text-brand hover:underline">Details →</Link>}>
        {recommendationState.loading && <LoadingState label="Generating recommendations…" />}
        {recommendationState.error && <ErrorState error={recommendationState.error} onRetry={recommendationState.reload} />}
        {recommendationState.data && <RecommendationCard recommendation={recommendationState.data} />}
      </Panel>
    </div>
  );
}
