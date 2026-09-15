import { useSearchParams, Link } from "react-router-dom";
import { useApi } from "../hooks/useApi";
import { get } from "../services/api";
import { LoadingState, ErrorState } from "../components/LoadingState.jsx";
import Panel from "../components/Panel.jsx";
import LotPicker from "../components/LotPicker.jsx";
import RiskBadge from "../components/RiskBadge.jsx";
import { formatYield } from "../utils/format";

export default function SimilarLots() {
  const [params] = useSearchParams();
  const lotId = params.get("lot_id");

  const { data, loading, error, reload } = useApi(() => (lotId ? get(`/similar/${lotId}`, { k: 8 }) : Promise.resolve(null)), [lotId]);

  if (!lotId) return <LotPicker title="Similar Lots" basePath="/similar-lots" />;
  if (loading) return <LoadingState label="Searching historical lots…" />;
  if (error) return <ErrorState error={error} onRetry={reload} />;

  const current = data.current_process_parameters || {};

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-ink-primary">
          Similar Lots — <Link to={`/lots/${lotId}`} className="text-brand hover:underline font-mono">{lotId}</Link>
        </h1>
        <p className="text-sm text-ink-secondary">{data.insight}</p>
      </div>

      <Panel title="Current Lot Process Parameters">
        <div className="flex flex-wrap gap-6 text-sm">
          {Object.entries(current).map(([k, v]) => (
            <div key={k}>
              <span className="text-ink-muted">{k.replace("_", " ")}: </span>
              <span className="text-ink-primary font-medium tabular-nums">{typeof v === "number" ? v.toFixed(2) : v}</span>
            </div>
          ))}
        </div>
      </Panel>

      <Panel title="Most Similar Historical Lots" subtitle="Nearest neighbors on standardized process parameters, same process step">
        <div className="space-y-2">
          {data.similar_lots.map((lot) => (
            <Link
              key={lot.lot_id}
              to={`/lots/${lot.lot_id}`}
              className="flex items-center justify-between p-3 rounded-md border border-surface-border hover:border-brand/40 hover:bg-surface-raised/40 transition-colors"
            >
              <span className="font-mono text-sm text-ink-primary">{lot.lot_id}</span>
              <span className="text-xs text-ink-muted">{lot.equipment}</span>
              <span className="text-sm text-ink-secondary tabular-nums">Yield {formatYield(lot.yield)}</span>
              <RiskBadge level={lot.risk_level} size="sm" />
              <span className="text-xs text-brand tabular-nums font-medium">{Math.round(lot.similarity_score * 100)}% similar</span>
            </Link>
          ))}
        </div>
      </Panel>
    </div>
  );
}
