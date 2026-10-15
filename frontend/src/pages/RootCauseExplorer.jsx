import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useApi } from "../hooks/useApi";
import { get } from "../services/api";
import Panel from "../components/Panel.jsx";
import LotPicker from "../components/LotPicker.jsx";
import EvidenceGraph from "../components/EvidenceGraph.jsx";
import SyntheticNotice from "../components/SyntheticNotice.jsx";
import { EmptyState, ErrorState, LoadingState } from "../components/LoadingState.jsx";
import { formatNumber, formatYield } from "../utils/format";
import { ActionList } from "./LotInvestigation.jsx";

/**
 * Root Cause Explorer (Phase 10): cause → evidence → historical cases → recommendation,
 * with the evidence graph. Selecting a cause filters the evidence and the actions.
 */
export default function RootCauseExplorer() {
  const [params] = useSearchParams();
  const lotId = params.get("lot_id");
  if (!lotId) return <LotPicker title="Root cause explorer" basePath="/root-cause-explorer" />;
  return <Explorer lotId={lotId} />;
}

function Explorer({ lotId }) {
  const rc = useApi(() => get(`/root-cause/${lotId}`), [lotId]);
  const similar = useApi(() => get(`/similar/${lotId}`), [lotId]);
  const recs = useApi(() => get(`/recommendations/${lotId}`), [lotId]);
  const graph = useApi(() => get(`/root-cause/${lotId}/graph`), [lotId]);
  const [selected, setSelected] = useState(null);

  if (rc.loading) return <LoadingState label="Ranking candidates…" />;
  if (rc.error) return <ErrorState error={rc.error} onRetry={rc.reload} />;

  const candidates = rc.data?.candidates || [];
  const cause = candidates.find((c) => c.candidate === selected) || candidates[0];
  const actions = (recs.data?.actions || []).filter((a) => a.cause === cause?.candidate);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-ink-primary">Root cause explorer · <span className="font-mono">{lotId}</span></h1>
          <p className="text-xs text-ink-muted">Ranked candidates with evidence — correlation, not proof of cause.</p>
        </div>
        <Link to={`/investigate?lot_id=${lotId}`} className="text-xs text-brand">Open lot investigation</Link>
      </div>
      <SyntheticNotice />
      <div className="grid lg:grid-cols-3 gap-4">
        <Panel title="1. Cause" subtitle="Select a candidate">
          {candidates.length === 0 ? (
            <EmptyState label="The engine returned no candidates." />
          ) : (
            <ul className="space-y-1">
              {candidates.map((c) => (
                <li key={c.candidate}>
                  <button
                    onClick={() => setSelected(c.candidate)}
                    aria-pressed={cause?.candidate === c.candidate}
                    className={`w-full flex justify-between rounded px-2 py-1.5 text-sm ${cause?.candidate === c.candidate ? "bg-brand/10 text-brand" : "text-ink-secondary hover:bg-surface-raised"}`}
                  >
                    <span>{c.candidate}</span>
                    <span className="font-mono text-xs">{formatNumber(c.score)} · {c.confidence}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <Panel title="2. Evidence" subtitle={cause ? `Supporting items for ${cause.candidate}` : undefined} className="lg:col-span-2">
          {!cause?.evidence?.length ? (
            <EmptyState label="No evidence items for this candidate." />
          ) : (
            <ul className="divide-y divide-surface-border" data-testid="evidence-list">
              {cause.evidence.map((e) => (
                <li key={e.evidence_id || e.label} className="py-2 text-sm">
                  <div className="text-ink-primary">{e.label}</div>
                  <div className="text-[11px] text-ink-muted font-mono">{e.evidence_id} · {e.source}</div>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
      <div className="grid lg:grid-cols-2 gap-4">
        <Panel title="3. Historical cases" subtitle="Most similar past lots and their outcomes">
          {similar.loading ? (
            <LoadingState label="Searching history…" />
          ) : similar.error ? (
            <ErrorState error={similar.error} onRetry={similar.reload} />
          ) : (
            <div className="space-y-2 text-sm">
              <p className="text-xs text-ink-muted">{similar.data?.insight}</p>
              <ul className="divide-y divide-surface-border">
                {(similar.data?.similar_lots || []).map((s) => (
                  <li key={s.lot_id} className="py-1.5 flex justify-between">
                    <Link to={`/investigate?lot_id=${s.lot_id}`} className="font-mono text-brand">{s.lot_id}</Link>
                    <span className="text-xs text-ink-secondary">{s.equipment}</span>
                    <span className="font-mono">{formatYield(s.yield)}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Panel>
        <Panel title="4. Recommendation" subtitle={cause ? `Actions for ${cause.candidate}` : undefined}>
          {recs.loading ? (
            <LoadingState label="Scoring actions…" />
          ) : recs.error ? (
            <ErrorState error={recs.error} onRetry={recs.reload} />
          ) : (
            <ActionList actions={actions} />
          )}
        </Panel>
      </div>
      <Panel title="Evidence graph" subtitle="Solid = measured on this lot · dashed = rule from graph_rules.json">
        {graph.loading ? (
          <LoadingState label="Building graph…" />
        ) : graph.error ? (
          <ErrorState error={graph.error} onRetry={graph.reload} />
        ) : (
          <EvidenceGraph graph={graph.data} />
        )}
      </Panel>
    </div>
  );
}
