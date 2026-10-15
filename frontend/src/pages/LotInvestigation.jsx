import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useApi } from "../hooks/useApi";
import { get, post } from "../services/api";
import Panel from "../components/Panel.jsx";
import RiskBadge from "../components/RiskBadge.jsx";
import LotPicker from "../components/LotPicker.jsx";
import RangeBar from "../components/RangeBar.jsx";
import SyntheticNotice from "../components/SyntheticNotice.jsx";
import { EmptyState, ErrorState, LoadingState } from "../components/LoadingState.jsx";
import { formatDate, formatNumber, formatYield } from "../utils/format";

export const WHATIF_LABEL = "Model-based counterfactual. Not proof that this change will produce this result.";
const NOTABLE_EVENTS = ["PARAMETER_EXCURSION", "EQUIPMENT_ALARM", "RECIPE_TRANSITION", "MAINTENANCE", "INSPECTION"];

/**
 * Lot Investigation (Phase 10): one lot, read top to bottom as a decision:
 *   1 risk → 2 why → 3 what happened → 4 what to investigate → 5 what-if.
 * Every section loads (and fails) on its own, so one missing model never blanks the page.
 */
export default function LotInvestigation() {
  const [params] = useSearchParams();
  const lotId = params.get("lot_id");
  if (!lotId) return <LotPicker title="Lot investigation" basePath="/investigate" />;
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-ink-primary">Lot investigation · <span className="font-mono">{lotId}</span></h1>
          <p className="text-xs text-ink-muted">Risk → why → what happened → what to investigate → what-if</p>
        </div>
        <Link to="/investigate" className="text-xs text-brand">Pick another lot</Link>
      </div>
      <SyntheticNotice />
      <RiskSection lotId={lotId} />
      <WhySection lotId={lotId} />
      <WhatHappenedSection lotId={lotId} />
      <InvestigateSection lotId={lotId} />
      <WhatIfSection lotId={lotId} />
    </div>
  );
}

function Section({ step, title, subtitle, children }) {
  return (
    <Panel title={`${step}. ${title}`} subtitle={subtitle}>
      {children}
    </Panel>
  );
}

function Loadable({ state, label, children }) {
  if (state.loading) return <LoadingState label={label} />;
  if (state.error) return <ErrorState error={state.error} onRetry={state.reload} />;
  if (!state.data) return <EmptyState />;
  return children(state.data);
}

function RiskSection({ lotId }) {
  const state = useApi(() => get(`/lots/${lotId}`), [lotId]);
  return (
    <Section step={1} title="Risk" subtitle="Stored model prediction for this lot">
      <Loadable state={state} label="Loading lot…">
        {(lot) => (
          <div className="flex flex-wrap items-center gap-6 text-sm">
            <RiskBadge level={lot.risk_level} probability={lot.risk_probability} />
            <div>
              <div className="label-caps">Predicted yield</div>
              <div className="font-mono text-ink-primary">{formatYield(lot.predicted_yield)}</div>
            </div>
            <div>
              <div className="label-caps">Measured yield</div>
              <div className="font-mono text-ink-primary">{formatYield(lot.yield)}</div>
            </div>
            <div>
              <div className="label-caps">Tool / step</div>
              <div className="text-ink-primary">{lot.equipment} · {lot.process_step}</div>
            </div>
            <div>
              <div className="label-caps">Started</div>
              <div className="text-ink-primary">{formatDate(lot.process_start)}</div>
            </div>
          </div>
        )}
      </Loadable>
    </Section>
  );
}

function WhySection({ lotId }) {
  const state = useApi(() => get(`/root-cause/${lotId}`), [lotId]);
  return (
    <Section step={2} title="Why" subtitle="Ranked root-cause candidates with evidence — candidates, not confirmed causes">
      <Loadable state={state} label="Ranking candidates…">
        {(rc) => (
          <ol className="space-y-2">
            {(rc.candidates || []).slice(0, 3).map((c) => (
              <li key={c.candidate} className="text-sm">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-ink-primary">{c.candidate}</span>
                  <span className="text-xs font-mono text-ink-muted">score {formatNumber(c.score)}</span>
                  <span className="text-[11px] text-ink-secondary">{c.confidence} confidence</span>
                </div>
                {c.evidence?.[0] && <div className="text-xs text-ink-muted mt-0.5">{c.evidence[0].label}</div>}
              </li>
            ))}
          </ol>
        )}
      </Loadable>
    </Section>
  );
}

function WhatHappenedSection({ lotId }) {
  const defects = useApi(() => get(`/defects/lots/${lotId}`), [lotId]);
  const timeline = useApi(() => get(`/lots/${lotId}/timeline`), [lotId]);
  const events = (timeline.data?.events || []).filter((e) => NOTABLE_EVENTS.includes(e.event_type));
  return (
    <Section step={3} title="What happened" subtitle="Inspection result and the run's event timeline">
      <div className="grid md:grid-cols-2 gap-4">
        <Loadable state={defects} label="Loading defects…">
          {(d) => (
            <div className="text-sm space-y-1">
              <div>Inspection pattern: <span className="font-medium text-ink-primary">{d.inspection_pattern || "none"}</span></div>
              <div>Defect density: <span className="font-mono">{formatNumber(d.defect_density, 3)}</span></div>
              <div>Failed wafers: <span className="font-mono">{d.n_failed_wafers}/{d.n_wafers}</span></div>
              <div className="text-xs text-ink-muted">
                By type: {Object.entries(d.by_type || {}).map(([k, v]) => `${k} ${v}`).join(" · ") || "—"}
              </div>
            </div>
          )}
        </Loadable>
        {timeline.loading ? (
          <LoadingState label="Loading timeline…" />
        ) : timeline.error ? (
          <ErrorState error={timeline.error} onRetry={timeline.reload} />
        ) : (
          <ul className="text-xs space-y-1 max-h-48 overflow-y-auto">
            {events.length === 0 && <li className="text-ink-muted">No notable events logged.</li>}
            {events.map((e, i) => (
              <li key={i} className="flex gap-2">
                <span className="font-mono text-ink-muted">{formatDate(e.ts)}</span>
                <span className="text-ink-primary">{e.event_type.replace(/_/g, " ").toLowerCase()}</span>
                <span className="text-ink-secondary">{e.payload?.parameter || e.payload?.alarm || e.payload?.maintenance_type || ""}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Section>
  );
}

export function ActionList({ actions }) {
  if (!actions?.length) return <EmptyState label="No evidence-backed action for this lot's candidates." />;
  return (
    <ul className="divide-y divide-surface-border">
      {actions.map((a) => (
        <li key={a.action_id} className="py-2 text-sm">
          <div className="text-ink-primary">{a.text}</div>
          <div className="text-xs text-ink-muted mt-0.5 flex flex-wrap gap-x-3">
            <span>cause: {a.cause}</span>
            <span>
              recovered <span className="font-mono text-ink-primary">{a.recovery_text}</span> similar past cases
            </span>
            {a.avg_yield_change !== null && <span>avg yield change {a.avg_yield_change > 0 ? "+" : ""}{a.avg_yield_change} pp</span>}
          </div>
          {a.small_sample_warning && <div className="text-xs text-status-warning mt-0.5">{a.small_sample_warning}</div>}
        </li>
      ))}
    </ul>
  );
}

function InvestigateSection({ lotId }) {
  const state = useApi(() => get(`/recommendations/${lotId}`), [lotId]);
  return (
    <Section step={4} title="What to investigate" subtitle="Knowledge-base actions, scored by the (synthetic) intervention history">
      <Loadable state={state} label="Scoring actions…">
        {(rec) => (
          <div className="space-y-2">
            <ActionList actions={rec.actions?.slice(0, 5)} />
            <p className="text-[11px] text-ink-muted">{rec.label}</p>
          </div>
        )}
      </Loadable>
    </Section>
  );
}

function WhatIfSection({ lotId }) {
  const limits = useApi(() => get("/whatif/limits"), []);
  const [parameter, setParameter] = useState("rf_power");
  const [value, setValue] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [running, setRunning] = useState(false);
  const spec = limits.data?.parameters?.[parameter];

  async function run(event) {
    event.preventDefault();
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      setResult(await post("/whatif", { lot_id: lotId, changes: { [parameter]: Number(value) } }));
    } catch (err) {
      setError(err);
    } finally {
      setRunning(false);
    }
  }

  return (
    <Section step={5} title="What-if" subtitle={WHATIF_LABEL}>
      <p className="text-xs font-medium text-status-warning mb-3" role="note">{WHATIF_LABEL}</p>
      {limits.loading ? (
        <LoadingState label="Loading limits…" />
      ) : limits.error ? (
        <ErrorState error={limits.error} onRetry={limits.reload} />
      ) : (
        <form onSubmit={run} className="flex flex-wrap items-end gap-3 text-sm">
          <label className="flex flex-col gap-1">
            <span className="label-caps">Parameter</span>
            <select aria-label="parameter" value={parameter} onChange={(e) => setParameter(e.target.value)} className="bg-surface-raised border border-surface-border rounded px-2 py-1">
              {Object.keys(limits.data.parameters).map((name) => (
                <option key={name} value={name}>{name}</option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="label-caps">New value {spec ? `(${spec.unit}, recipe ${spec.recipe_window[0]}–${spec.recipe_window[1]})` : ""}</span>
            <input aria-label="value" type="number" step="any" required value={value} onChange={(e) => setValue(e.target.value)} className="bg-surface-raised border border-surface-border rounded px-2 py-1 font-mono w-40" />
          </label>
          <button type="submit" disabled={running} className="text-xs font-medium text-brand border border-brand/30 rounded px-3 py-1.5">
            {running ? "Running…" : "Run counterfactual"}
          </button>
        </form>
      )}
      {error && <div className="mt-3"><ErrorState error={error} /></div>}
      {result && (
        <div className="mt-4 grid md:grid-cols-2 gap-4">
          <div className="space-y-3">
            <RangeBar label="Yield before" band={result.yield_before} />
            <RangeBar label="Yield after" band={result.yield_after} />
          </div>
          <div className="text-sm space-y-1">
            <div>
              Yield difference: <span className="font-mono text-ink-primary">{result.yield_difference.p50} pp</span>{" "}
              <span className="text-ink-muted">(P10 {result.yield_difference.p10} – P90 {result.yield_difference.p90})</span>
            </div>
            <div className="text-xs text-ink-muted">
              Members agreeing on direction: {result.yield_difference.members_agreeing_on_direction ?? "—"} · measured coverage of the 80% interval:{" "}
              {result.uncertainty_method.measured_test_coverage_of_interval_80}
            </div>
            <div className="text-xs">Risk level: {result.risk_level.before} → {result.risk_level.after}</div>
            {result.warnings?.map((w) => (
              <div key={w} className="text-xs text-status-warning">{w}</div>
            ))}
          </div>
        </div>
      )}
    </Section>
  );
}
