import { useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { apiRequest } from "../services/api";

const PARAMETERS = [
  { key: "temperature", label: "Temperature" },
  { key: "pressure", label: "Pressure" },
  { key: "rf_power", label: "RF Power" },
  { key: "gas_flow", label: "Gas Flow" },
];

function useEquipmentList() {
  const [equipment, setEquipment] = useState([]);
  useEffect(() => {
    let cancelled = false;
    apiRequest({ method: "GET", url: "/equipment" })
      .then((data) => {
        if (!cancelled) setEquipment(Array.isArray(data) ? data : []);
      })
      .catch(() => {
        if (!cancelled) setEquipment([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return equipment;
}

export default function ControlCharts() {
  const [equipmentId, setEquipmentId] = useState("");
  const [parameter, setParameter] = useState("rf_power");
  const [chart, setChart] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const equipment = useEquipmentList();

  useEffect(() => {
    if (!equipmentId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    apiRequest({ method: "GET", url: `/equipment/${equipmentId}/control-chart`, params: { parameter } })
      .then((data) => {
        if (!cancelled) {
          setChart(data);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setChart(null);
          setError(err.message);
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [equipmentId, parameter]);

  const breachLots = useMemo(() => new Set((chart?.breaches || []).map((b) => b.lot_id)), [chart]);
  const points = useMemo(
    () =>
      (chart?.points || []).map((p) => ({
        ...p,
        breach: breachLots.has(p.lot_id),
      })),
    [chart, breachLots]
  );

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-ink-primary">Control Charts</h1>
        <p className="text-sm text-ink-muted mt-1">
          Lot-level SPC per tool (running mean ± 3σ from the tool&apos;s own prior history; R1/R2/R3
          rules). Not per-reading SPC — granularity is honest to the data the risk engine uses.
        </p>
      </div>

      <div className="flex flex-wrap gap-3 items-end">
        <label className="space-y-1">
          <div className="text-xs text-ink-muted">Equipment</div>
          <select
            className="bg-surface-panel border border-surface-border rounded px-3 py-2 text-sm"
            value={equipmentId}
            onChange={(e) => setEquipmentId(e.target.value)}
          >
            <option value="">Select equipment…</option>
            {equipment.map((eq) => (
              <option key={eq.equipment_id} value={eq.equipment_id}>
                {eq.equipment_id}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1">
          <div className="text-xs text-ink-muted">Parameter</div>
          <select
            className="bg-surface-panel border border-surface-border rounded px-3 py-2 text-sm"
            value={parameter}
            onChange={(e) => setParameter(e.target.value)}
          >
            {PARAMETERS.map((p) => (
              <option key={p.key} value={p.key}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        {loading && <span className="text-sm text-ink-muted">Loading chart…</span>}
        {error && <span className="text-sm text-risk-high">{error}</span>}
      </div>

      {chart && (
        <div className="bg-surface-panel border border-surface-border rounded-lg p-4">
          <div className="flex gap-6 text-xs text-ink-muted mb-3 flex-wrap">
            <span>
              centerline <span className="text-ink-primary">{chart.centerline}</span>
            </span>
            <span>
              σ <span className="text-ink-primary">{chart.sigma}</span>
            </span>
            <span>
              UCL/LCL <span className="text-ink-primary">{chart.ucl ?? "—"} / {chart.lcl ?? "—"}</span>
            </span>
            <span>
              n <span className="text-ink-primary">{chart.n_history}</span>
            </span>
            {!chart.sigma_reliable && <span className="text-ink-muted">(σ unreliable below 8 prior lots — no rule flags)</span>}
          </div>
          <ResponsiveContainer width="100%" height={340}>
            <LineChart data={points} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e2a3a" />
              <XAxis dataKey="lot_id" tick={{ fontSize: 10, fill: "#64748b" }} interval="preserveStartEnd" />
              <YAxis tick={{ fontSize: 10, fill: "#64748b" }} domain={["auto", "auto"]} />
              <Tooltip
                contentStyle={{ background: "#0f172a", border: "1px solid #1e2a3a", fontSize: 12 }}
                formatter={(value, name) => [value, name]}
              />
              {chart.ucl != null && <ReferenceLine y={chart.ucl} stroke="#f59e0b" strokeDasharray="4 4" />}
              {chart.lcl != null && <ReferenceLine y={chart.lcl} stroke="#f59e0b" strokeDasharray="4 4" />}
              <ReferenceLine y={chart.centerline} stroke="#38bdf8" />
              <Line type="monotone" dataKey="value" name={parameter} stroke="#38bdf8" dot={(props) => {
                const isBreach = props.payload?.breach;
                return (
                  <circle
                    cx={props.cx}
                    cy={props.cy}
                    r={isBreach ? 5 : 3}
                    fill={isBreach ? "#ef4444" : "#38bdf8"}
                    stroke={isBreach ? "#fecaca" : "none"}
                  />
                );
              }} strokeWidth={1.5} />
            </LineChart>
          </ResponsiveContainer>

          <div className="mt-3 text-xs text-ink-muted">
            {chart.breaches.length === 0 ? (
              <span>No rule breaches in this window.</span>
            ) : (
              <ul className="space-y-1">
                {chart.breaches.map((b, i) => (
                  <li key={`${b.lot_id}-${b.rule}-${i}`} className="text-risk-high">
                    {b.lot_id}: {b.rule} — {b.detail}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}

      {!chart && !error && !loading && (
        <div className="text-sm text-ink-muted">Pick an equipment and parameter to view its control chart.</div>
      )}
    </div>
  );
}
