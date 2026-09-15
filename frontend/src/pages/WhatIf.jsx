import { useEffect, useState } from "react";
import { useSearchParams, Link } from "react-router-dom";
import { useApi } from "../hooks/useApi";
import { get, post } from "../services/api";
import { LoadingState, ErrorState } from "../components/LoadingState.jsx";
import Panel from "../components/Panel.jsx";
import LotPicker from "../components/LotPicker.jsx";
import RiskBadge from "../components/RiskBadge.jsx";
import { formatYield } from "../utils/format";

const SLIDER_RANGES = {
  temperature: { min: 400, max: 480, step: 0.5, unit: "°C" },
  pressure: { min: 1.8, max: 3.6, step: 0.02, unit: "Torr" },
  rf_power: { min: 780, max: 960, step: 1, unit: "W" },
  gas_flow: { min: 30, max: 75, step: 0.5, unit: "sccm" },
  etch_time: { min: 18, max: 52, step: 0.2, unit: "sec" },
  voltage: { min: 170, max: 290, step: 1, unit: "V" },
  current: { min: 4, max: 22, step: 0.1, unit: "A" },
};

export default function WhatIf() {
  const [params] = useSearchParams();
  const lotId = params.get("lot_id");

  const lotState = useApi(() => (lotId ? get(`/lots/${lotId}`) : Promise.resolve(null)), [lotId]);
  const [overrides, setOverrides] = useState(null);
  const [result, setResult] = useState(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState(null);

  useEffect(() => {
    if (lotState.data?.process_parameters) {
      setOverrides({ ...lotState.data.process_parameters });
      setResult(null);
    }
  }, [lotState.data]);

  if (!lotId) return <LotPicker title="What-If Simulator" basePath="/what-if" />;
  if (lotState.loading || !overrides) return <LoadingState label="Loading lot…" />;
  if (lotState.error) return <ErrorState error={lotState.error} onRetry={lotState.reload} />;

  const lot = lotState.data;

  async function runSimulation() {
    setRunning(true);
    setRunError(null);
    try {
      const data = await post("/what-if", { lot_id: lotId, overrides });
      setResult(data);
    } catch (err) {
      setRunError(err);
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-ink-primary">
          What-If Simulator — <Link to={`/lots/${lotId}`} className="text-brand hover:underline font-mono">{lotId}</Link>
        </h1>
        <p className="text-sm text-ink-secondary">
          Model-based what-if simulation — not a guarantee of physical outcome. Nothing here is written back to the lot or equipment.
        </p>
      </div>

      <div className="grid lg:grid-cols-2 gap-4">
        <Panel title="Adjust Process Parameters">
          <div className="space-y-4">
            {Object.entries(SLIDER_RANGES).map(([key, { min, max, step, unit }]) => (
              <div key={key}>
                <div className="flex items-center justify-between text-xs mb-1">
                  <span className="text-ink-secondary capitalize">{key.replace("_", " ")}</span>
                  <span className="text-ink-primary font-medium tabular-nums">
                    {overrides[key]?.toFixed(2)} {unit}
                  </span>
                </div>
                <input
                  type="range"
                  min={min}
                  max={max}
                  step={step}
                  value={overrides[key] ?? 0}
                  onChange={(e) => setOverrides((prev) => ({ ...prev, [key]: parseFloat(e.target.value) }))}
                  className="w-full accent-brand"
                />
              </div>
            ))}
          </div>
          <button
            onClick={runSimulation}
            disabled={running}
            className="mt-5 w-full py-2 rounded-md bg-brand text-surface font-medium text-sm hover:bg-brand/90 disabled:opacity-50"
          >
            {running ? "Running simulation…" : "Run Simulation"}
          </button>
          {runError && <p className="text-xs text-status-critical mt-2">{runError.message}</p>}
        </Panel>

        <Panel title="Result">
          {!result ? (
            <div className="text-sm text-ink-muted py-8 text-center">Adjust parameters and run the simulation to see before/after.</div>
          ) : (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-lg border border-surface-border p-3">
                  <div className="label-caps mb-2">Current (Before)</div>
                  <div className="text-2xl font-semibold text-ink-primary tabular-nums">{formatYield(result.before.predicted_yield)}</div>
                  <div className="mt-1"><RiskBadge level={result.before.risk_level} probability={result.before.risk_probability} size="sm" /></div>
                </div>
                <div className="rounded-lg border border-brand/40 bg-brand/5 p-3">
                  <div className="label-caps text-brand mb-2">Simulation (After)</div>
                  <div className="text-2xl font-semibold text-ink-primary tabular-nums">{formatYield(result.after.predicted_yield)}</div>
                  <div className="mt-1"><RiskBadge level={result.after.risk_level} probability={result.after.risk_probability} size="sm" /></div>
                </div>
              </div>
              <div className="text-sm text-ink-secondary">
                Δ Yield:{" "}
                <span className={result.delta.predicted_yield >= 0 ? "text-status-healthy font-medium" : "text-status-critical font-medium"}>
                  {result.delta.predicted_yield >= 0 ? "+" : ""}
                  {result.delta.predicted_yield}
                </span>
              </div>
              <p className="text-[11px] text-ink-muted italic">{result.label}</p>
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}
