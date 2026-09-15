import { useSearchParams, useNavigate, Link } from "react-router-dom";
import { useApi } from "../hooks/useApi";
import { get } from "../services/api";
import { LoadingState, ErrorState } from "../components/LoadingState.jsx";
import Panel from "../components/Panel.jsx";
import LotPicker from "../components/LotPicker.jsx";
import WaferMapViewer from "../components/WaferMapViewer.jsx";
import DataProvenanceBadge from "../components/DataProvenanceBadge.jsx";

export default function WaferMapPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const lotId = params.get("lot_id");
  const waferId = params.get("wafer_id");

  const wafersState = useApi(() => (lotId ? get("/wafers", { lot_id: lotId }) : Promise.resolve(null)), [lotId]);
  const effectiveWaferId = waferId || wafersState.data?.[0]?.wafer_id;
  const mapState = useApi(() => (effectiveWaferId ? get(`/wafers/${effectiveWaferId}/map`) : Promise.resolve(null)), [effectiveWaferId]);

  if (!lotId) return <LotPicker title="Wafer Map" basePath="/wafer-map" />;
  if (wafersState.loading) return <LoadingState label="Loading wafers…" />;
  if (wafersState.error) return <ErrorState error={wafersState.error} onRetry={wafersState.reload} />;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-ink-primary">
          Wafer Map — <Link to={`/lots/${lotId}`} className="text-brand hover:underline font-mono">{lotId}</Link>
        </h1>
        <p className="text-sm text-ink-secondary">Spatial defect pattern classification and visualization.</p>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {wafersState.data.map((w) => (
          <button
            key={w.wafer_id}
            onClick={() => navigate(`/wafer-map?lot_id=${lotId}&wafer_id=${w.wafer_id}`, { replace: true })}
            className={`px-2.5 py-1 rounded text-xs font-mono border ${
              w.wafer_id === effectiveWaferId
                ? "border-brand bg-brand/15 text-brand"
                : "border-surface-border text-ink-secondary hover:text-ink-primary"
            }`}
          >
            {w.slot}
          </button>
        ))}
      </div>

      {mapState.loading && <LoadingState label="Classifying wafer map…" />}
      {mapState.error && <ErrorState error={mapState.error} onRetry={mapState.reload} />}
      {mapState.data && (
        <div className="grid lg:grid-cols-3 gap-4">
          <Panel className="lg:col-span-2" title={effectiveWaferId}>
            <WaferMapViewer dieGrid={mapState.data.die_grid} gradCam={mapState.data.grad_cam} />
          </Panel>
          <Panel title="Classification">
            <div className="mb-2">
              <DataProvenanceBadge source={mapState.data.data_source} />
            </div>
            <div className="text-2xl font-semibold text-ink-primary">{mapState.data.predicted_pattern || mapState.data.wafer_map_pattern}</div>
            <div className="text-sm text-ink-muted mt-0.5">
              Confidence: {Math.round((mapState.data.confidence || mapState.data.wafer_map_confidence || 0) * 100)}%
            </div>

            {mapState.data.alternatives && (
              <div className="mt-4">
                <div className="label-caps mb-2">Alternatives</div>
                <div className="space-y-1.5">
                  {mapState.data.alternatives.map((alt) => (
                    <div key={alt.pattern} className="flex justify-between text-xs">
                      <span className="text-ink-secondary">{alt.pattern}</span>
                      <span className="text-ink-muted tabular-nums">{Math.round(alt.probability * 100)}%</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {mapState.data.spatial_stats && (
              <div className="mt-4 pt-4 border-t border-surface-border">
                <div className="label-caps mb-2">Spatial Analysis</div>
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <div><span className="text-ink-muted">Defect density: </span><span className="text-ink-primary">{(mapState.data.spatial_stats.defect_density * 100).toFixed(1)}%</span></div>
                  <div><span className="text-ink-muted">Dominant zone: </span><span className="text-ink-primary">{mapState.data.spatial_stats.dominant_zone}</span></div>
                  <div><span className="text-ink-muted">Cluster count: </span><span className="text-ink-primary">{mapState.data.spatial_stats.cluster_count}</span></div>
                  <div><span className="text-ink-muted">Edge fail ratio: </span><span className="text-ink-primary">{(mapState.data.spatial_stats.edge_fail_ratio * 100).toFixed(1)}%</span></div>
                </div>
                <p className="text-[11px] text-ink-muted mt-2 italic">Potential process/equipment association — not proof of a specific cause.</p>
              </div>
            )}
          </Panel>
        </div>
      )}
    </div>
  );
}
