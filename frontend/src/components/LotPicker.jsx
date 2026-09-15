import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useApi } from "../hooks/useApi";
import { get } from "../services/api";
import RiskBadge from "./RiskBadge.jsx";

/** Shown on standalone investigation pages (Root Cause, Recommendations, Similar Lots, What-If)
 * when no ?lot_id= is in the URL yet — search and pick one. */
export default function LotPicker({ title, basePath }) {
  const [query, setQuery] = useState("");
  const navigate = useNavigate();
  const { data } = useApi(() => get("/lots", { q: query || undefined, limit: 8, risk_level: query ? undefined : "HIGH" }), [query]);

  return (
    <div className="panel p-6 max-w-xl">
      <h2 className="text-sm font-semibold text-ink-primary mb-1">{title}</h2>
      <p className="text-xs text-ink-muted mb-4">Search for a lot to get started, or pick one of the current high-risk lots.</p>
      <input
        autoFocus
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search lot ID, product, equipment…"
        className="w-full bg-surface-raised border border-surface-border rounded-md px-3 py-2 text-sm text-ink-primary placeholder:text-ink-muted focus:outline-none focus:border-brand/50"
      />
      <div className="mt-3 divide-y divide-surface-border">
        {(data?.items || []).map((lot) => (
          <button
            key={lot.lot_id}
            onClick={() => navigate(`${basePath}?lot_id=${lot.lot_id}`)}
            className="w-full flex items-center justify-between py-2 text-left hover:bg-surface-raised/60 rounded px-1"
          >
            <span className="font-mono text-sm text-ink-primary">{lot.lot_id}</span>
            <span className="text-xs text-ink-muted">{lot.equipment}</span>
            <RiskBadge level={lot.risk_level} size="sm" />
          </button>
        ))}
      </div>
    </div>
  );
}
