import { useState } from "react";
import { useApi } from "../hooks/useApi";
import { get } from "../services/api";
import { LoadingState, ErrorState } from "../components/LoadingState.jsx";
import Panel from "../components/Panel.jsx";
import EquipmentStatusCard from "../components/EquipmentStatusCard.jsx";
import LotRiskTable from "../components/LotRiskTable.jsx";
import { formatDate } from "../utils/format";

export default function Equipment() {
  const [selected, setSelected] = useState(null);
  const listState = useApi(() => get("/equipment"), []);
  const detailState = useApi(() => (selected ? get(`/equipment/${selected}`) : Promise.resolve(null)), [selected]);

  if (listState.loading) return <LoadingState label="Loading equipment…" />;
  if (listState.error) return <ErrorState error={listState.error} onRetry={listState.reload} />;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-ink-primary">Equipment</h1>
        <p className="text-sm text-ink-secondary">Tool/chamber health and anomaly status.</p>
      </div>

      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {listState.data.map((eq) => (
          <div key={eq.equipment_id} onClick={() => setSelected(eq.equipment_id)} className="cursor-pointer">
            <EquipmentStatusCard equipment={eq} />
          </div>
        ))}
      </div>

      {selected && (
        <Panel title={selected} subtitle="Recent lots, maintenance history, and peer chambers" action={<button onClick={() => setSelected(null)} className="text-xs text-ink-muted hover:text-ink-primary">Close ✕</button>}>
          {detailState.loading && <LoadingState label="Loading equipment detail…" />}
          {detailState.error && <ErrorState error={detailState.error} onRetry={detailState.reload} />}
          {detailState.data && (
            <div className="grid lg:grid-cols-2 gap-6">
              <div>
                <div className="label-caps mb-2">Recent Lots</div>
                <LotRiskTable lots={detailState.data.recent_lots} compact />
              </div>
              <div>
                <div className="label-caps mb-2">
                  Maintenance History
                  {detailState.data.days_since_last_maintenance !== null && (
                    <span className="text-ink-muted normal-case font-normal"> · {Math.round(detailState.data.days_since_last_maintenance)} days since last</span>
                  )}
                </div>
                <ul className="space-y-1.5 text-sm">
                  {detailState.data.recent_maintenance.map((m, idx) => (
                    <li key={idx} className="flex justify-between text-xs border-b border-surface-border/60 py-1.5">
                      <span className="text-ink-primary">{m.event_type.replace("_", " ")}</span>
                      <span className="text-ink-muted">{formatDate(m.timestamp)}</span>
                    </li>
                  ))}
                </ul>
                <div className="label-caps mt-4 mb-2">Peer Chambers</div>
                <div className="flex flex-wrap gap-1.5">
                  {detailState.data.peers.map((p) => (
                    <span key={p.equipment_id} className="text-xs font-mono px-2 py-1 rounded bg-surface-raised text-ink-secondary">
                      {p.equipment_id}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          )}
        </Panel>
      )}
    </div>
  );
}
