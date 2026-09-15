import { useState } from "react";
import { useApi } from "../hooks/useApi";
import { get } from "../services/api";
import { LoadingState, ErrorState } from "../components/LoadingState.jsx";
import Panel from "../components/Panel.jsx";
import LotRiskTable from "../components/LotRiskTable.jsx";

const PAGE_SIZE = 20;

export default function Lots() {
  const [search, setSearch] = useState("");
  const [riskLevel, setRiskLevel] = useState("");
  const [page, setPage] = useState(0);

  const { data, loading, error, reload } = useApi(
    () => get("/lots", { q: search || undefined, risk_level: riskLevel || undefined, skip: page * PAGE_SIZE, limit: PAGE_SIZE }),
    [search, riskLevel, page]
  );

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-ink-primary">Lots</h1>
        <p className="text-sm text-ink-secondary">Search, filter, and sort all lots.</p>
      </div>

      <div className="flex flex-wrap gap-3">
        <input
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(0);
          }}
          placeholder="Search lot ID, product, equipment…"
          className="flex-1 min-w-[200px] bg-surface-panel border border-surface-border rounded-md px-3 py-2 text-sm text-ink-primary placeholder:text-ink-muted focus:outline-none focus:border-brand/50"
        />
        <select
          value={riskLevel}
          onChange={(e) => {
            setRiskLevel(e.target.value);
            setPage(0);
          }}
          className="bg-surface-panel border border-surface-border rounded-md px-3 py-2 text-sm text-ink-primary focus:outline-none focus:border-brand/50"
        >
          <option value="">All risk levels</option>
          <option value="LOW">Low</option>
          <option value="MEDIUM">Medium</option>
          <option value="HIGH">High</option>
        </select>
      </div>

      {loading && <LoadingState label="Loading lots…" />}
      {error && <ErrorState error={error} onRetry={reload} />}
      {data && (
        <Panel>
          <LotRiskTable lots={data.items} />
          <div className="flex items-center justify-between mt-4 text-sm">
            <span className="text-ink-muted">
              {data.total} lots — page {page + 1} of {totalPages}
            </span>
            <div className="flex gap-2">
              <button
                disabled={page === 0}
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                className="px-2.5 py-1 rounded border border-surface-border text-ink-secondary disabled:opacity-40 hover:text-ink-primary"
              >
                Previous
              </button>
              <button
                disabled={page + 1 >= totalPages}
                onClick={() => setPage((p) => p + 1)}
                className="px-2.5 py-1 rounded border border-surface-border text-ink-secondary disabled:opacity-40 hover:text-ink-primary"
              >
                Next
              </button>
            </div>
          </div>
        </Panel>
      )}
    </div>
  );
}
