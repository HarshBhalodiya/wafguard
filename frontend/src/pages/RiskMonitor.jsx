import { useState } from "react";
import { useApi } from "../hooks/useApi";
import { get } from "../services/api";
import { LoadingState, ErrorState } from "../components/LoadingState.jsx";
import Panel from "../components/Panel.jsx";
import LotRiskTable from "../components/LotRiskTable.jsx";

export default function RiskMonitor() {
  const [riskLevel, setRiskLevel] = useState("");
  const { data, loading, error, reload } = useApi(() => get("/risk/lots", { risk_level: riskLevel || undefined, limit: 100 }), [riskLevel]);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-ink-primary">Risk Monitor</h1>
        <p className="text-sm text-ink-secondary">Upcoming lots likely to experience low yield.</p>
      </div>

      <div className="flex gap-2">
        {["", "HIGH", "MEDIUM"].map((level) => (
          <button
            key={level || "all"}
            onClick={() => setRiskLevel(level)}
            className={`px-3 py-1.5 rounded-md text-sm border ${
              riskLevel === level ? "border-brand/50 bg-brand/10 text-brand" : "border-surface-border text-ink-secondary hover:text-ink-primary"
            }`}
          >
            {level || "All at-risk"}
          </button>
        ))}
      </div>

      {loading && <LoadingState label="Loading at-risk lots…" />}
      {error && <ErrorState error={error} onRetry={reload} />}
      {data && (
        <Panel subtitle={`${data.length} lots`}>
          <LotRiskTable lots={data} />
        </Panel>
      )}
    </div>
  );
}
