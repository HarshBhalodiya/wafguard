import { useEffect, useState } from "react";
import { useApi } from "../hooks/useApi";
import { get } from "../services/api";
import { LoadingState, ErrorState } from "../components/LoadingState.jsx";
import Panel from "../components/Panel.jsx";
import EquipmentStatusCard from "../components/EquipmentStatusCard.jsx";
import LotRiskTable from "../components/LotRiskTable.jsx";
import { formatDate } from "../utils/format";

// P8-4: control-chart breach context comes from the existing P5-2 API — no new
// chart math here. Cards show the canonical drift channel (rf_power) over a
// short window; the opened detail shows all tracked parameters.
const SPC_PARAMETERS = [
  { key: "temperature", label: "Temperature" },
  { key: "pressure", label: "Pressure" },
  { key: "rf_power", label: "RF Power" },
  { key: "gas_flow", label: "Gas Flow" },
];
const CARD_SPC_PARAMETER = "rf_power";
const CARD_SPC_WINDOW = 40;

export default function Equipment() {
  const [selected, setSelected] = useState(null);
  const listState = useApi(() => get("/equipment"), []);
  const detailState = useApi(() => (selected ? get(`/equipment/${selected}`) : Promise.resolve(null)), [selected]);
  const spcDetail = useApi(
    () =>
      selected
        ? Promise.all(
            SPC_PARAMETERS.map((p) =>
              get(`/equipment/${selected}/control-chart`, { parameter: p.key }).catch(() => null)
            )
          )
        : Promise.resolve(null),
    [selected]
  );

  // One chart request per tool for the card grid, fired after the list renders.
  // Failures are omitted (the card then shows no SPC line) — a chart outage must
  // not break the equipment page.
  const [spcCounts, setSpcCounts] = useState({});
  useEffect(() => {
    const equipment = listState.data;
    if (!equipment?.length) return undefined;
    let cancelled = false;
    Promise.all(
      equipment.map((eq) =>
        get(`/equipment/${eq.equipment_id}/control-chart`, {
          parameter: CARD_SPC_PARAMETER,
          limit: CARD_SPC_WINDOW,
        })
          .then((chart) => [eq.equipment_id, { breaches: chart.breaches.length, window: CARD_SPC_WINDOW }])
          .catch(() => [eq.equipment_id, null])
      )
    ).then((pairs) => {
      if (!cancelled) setSpcCounts(Object.fromEntries(pairs.filter(([, value]) => value)));
    });
    return () => {
      cancelled = true;
    };
  }, [listState.data]);

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
            <EquipmentStatusCard equipment={eq} spc={spcCounts[eq.equipment_id]} />
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

                {/* P8-4: control-chart breach summary from the existing P5-2
                    endpoint. Lot-level SPC (D-031) — explicitly not per-reading. */}
                <div className="label-caps mt-4 mb-2">Control-Chart Context</div>
                <p className="text-[11px] text-ink-muted mb-2">
                  Lot-level SPC per parameter (running mean ±3σ vs the tool&apos;s own prior history) — not per-reading SPC.
                </p>
                {spcDetail.loading && <p className="text-xs text-ink-muted">Loading control-chart context…</p>}
                {spcDetail.data && (
                  <div className="grid grid-cols-2 gap-2">
                    {spcDetail.data.map((chart, idx) => {
                      const param = SPC_PARAMETERS[idx];
                      if (!chart) return null;
                      const n = chart.breaches.length;
                      return (
                        <div key={param.key} className="text-xs border border-surface-border rounded p-2">
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-ink-secondary">{param.label}</span>
                            <span className={n > 0 ? "text-status-critical" : "text-status-healthy"}>
                              {n} breach{n === 1 ? "" : "es"}
                            </span>
                          </div>
                          <div className="text-[10px] text-ink-muted mt-0.5">
                            lot-level · last {chart.points.length} lots
                            {!chart.sigma_reliable && " · σ unreliable (<8 prior lots)"}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
          )}
        </Panel>
      )}
    </div>
  );
}
