import { useEffect, useState } from "react";
import Panel from "../components/Panel.jsx";
import DataProvenanceBadge from "../components/DataProvenanceBadge.jsx";
import { get } from "../services/api.js";

export default function Settings() {
  const apiBase = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000/api";
  const username = localStorage.getItem("wafguard_username");
  const role = localStorage.getItem("wafguard_role");

  // P7-4: live operational-visibility panel (Phase 7). Admin-only numbers from
  // /api/ops/* — honest about being in-process only (reset on API restart).
  const [ops, setOps] = useState(null);
  const [opsError, setOpsError] = useState(null);
  useEffect(() => {
    if (role !== "admin") return undefined; // viewers/engineers never fire the request
    let cancelled = false;
    get("/ops/metrics", { window: 100 })
      .then((data) => {
        if (!cancelled) setOps(data);
      })
      .catch((err) => {
        if (!cancelled) setOpsError(err.message || "Could not load ops metrics.");
      });
    return () => {
      cancelled = true;
    };
  }, [role]);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-ink-primary">Settings</h1>
        <p className="text-sm text-ink-secondary">Session, connection, and data-source information.</p>
      </div>

      <Panel title="Session">
        {username ? (
          <div className="text-sm">
            <p className="text-ink-primary">Signed in as <span className="font-medium">{username}</span></p>
            <p className="text-ink-muted mt-0.5">Role: {role}</p>
            <button
              onClick={() => {
                localStorage.removeItem("wafguard_token");
                localStorage.removeItem("wafguard_username");
                localStorage.removeItem("wafguard_role");
                window.location.href = "/login";
              }}
              className="mt-3 text-xs px-3 py-1.5 rounded-md border border-surface-border text-ink-secondary hover:text-ink-primary"
            >
              Sign out
            </button>
          </div>
        ) : (
          <p className="text-sm text-ink-muted">Not signed in. Every page except login needs a JWT — open /login with a demo account.</p>
        )}
      </Panel>

      <Panel title="Connection">
        <div className="text-sm space-y-1.5">
          <p><span className="text-ink-muted">API base URL: </span><span className="text-ink-primary font-mono text-xs">{apiBase}</span></p>
        </div>
      </Panel>

      <Panel title="Data Sources" subtitle="What powers each part of this prototype">
        <div className="space-y-2.5 text-sm">
          <div className="flex items-center gap-2"><DataProvenanceBadge source="secom" /><span className="text-ink-secondary">Feature-analysis panel on Model Performance</span></div>
          <div className="flex items-center gap-2"><DataProvenanceBadge source="synthetic" /><span className="text-ink-secondary">Lots, equipment, sensors, maintenance, yield predictions</span></div>
          <div className="flex items-center gap-2"><DataProvenanceBadge source="wm811k_synthetic" /><span className="text-ink-secondary">Wafer maps and pattern classification</span></div>
        </div>
      </Panel>

      {role === "admin" && (
        <Panel
          title="Live Ops (admin only)"
          subtitle="Per-endpoint latency from the API's in-process ring buffer — resets when the backend restarts"
        >
          {opsError && <p className="text-sm text-red-400">Ops metrics unavailable: {opsError}</p>}
          {!ops && !opsError && <p className="text-sm text-ink-muted">Loading…</p>}
          {ops && (
            <div className="text-sm space-y-2">
              <p className="text-ink-secondary">
                {ops.window_spans} span(s) recorded in the current window.
              </p>
              {ops.endpoints.length === 0 ? (
                <p className="text-ink-muted">No traffic recorded yet.</p>
              ) : (
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-ink-muted text-left">
                      <th className="py-1 pr-2 font-normal">Endpoint</th>
                      <th className="py-1 pr-2 font-normal">Count</th>
                      <th className="py-1 pr-2 font-normal">p50</th>
                      <th className="py-1 pr-2 font-normal">p95</th>
                      <th className="py-1 font-normal">Max</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ops.endpoints.map((e) => (
                      <tr key={e.name} className="border-t border-surface-border">
                        <td className="py-1 pr-2 font-mono text-ink-primary">{e.name}</td>
                        <td className="py-1 pr-2 text-ink-secondary">{e.count}</td>
                        <td className="py-1 pr-2 text-ink-secondary">{e.p50_ms.toFixed(0)} ms</td>
                        <td className="py-1 pr-2 text-ink-secondary">{e.p95_ms.toFixed(0)} ms</td>
                        <td className="py-1 text-ink-secondary">{e.max_ms.toFixed(0)} ms</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </Panel>
      )}

      <Panel title="Limitations">
        <ul className="text-sm text-ink-secondary list-disc list-inside space-y-1">
          <li>UCI SECOM and WM-811K are unrelated public datasets — never presented as the same fab.</li>
          <li>Root-cause candidates are model-attribution-based, not causal proof.</li>
          <li>Wafer classification trains on synthetic maps until a real WM-811K file is provided.</li>
          <li>No light theme yet — dark control-room theme only.</li>
        </ul>
      </Panel>
    </div>
  );
}
