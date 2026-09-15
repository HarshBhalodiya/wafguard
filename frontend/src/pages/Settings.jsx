import Panel from "../components/Panel.jsx";
import DataProvenanceBadge from "../components/DataProvenanceBadge.jsx";

export default function Settings() {
  const apiBase = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000/api";
  const username = localStorage.getItem("wafguard_username");
  const role = localStorage.getItem("wafguard_role");

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
          <p className="text-sm text-ink-muted">Not signed in. Most pages work without signing in; importing data requires a login.</p>
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
