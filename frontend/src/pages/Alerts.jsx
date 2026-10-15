import { useState } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../hooks/useApi";
import { get, post } from "../services/api";
import { LoadingState, ErrorState, EmptyState } from "../components/LoadingState.jsx";
import { formatDate } from "../utils/format";

// P8-3: triage actions mirror the backend's per-action role matrix (P8-2) —
// viewer is read-only, so the buttons never render for it.
const TRIAGE_ROLES = ["admin", "process_engineer", "yield_engineer", "equipment_engineer"];

const SEVERITY_DOT = {
  HIGH: "bg-status-critical",
  MEDIUM: "bg-status-warning",
  LOW: "bg-status-info",
};

// Labels are uppercase literals (not CSS) so they are distinguishable from the
// filter dropdown's option texts in both the DOM and tests.
const STATUS_BADGE = {
  open: { label: "OPEN", cls: "text-status-warning border-status-warning/40" },
  acknowledged: { label: "ACKNOWLEDGED", cls: "text-status-info border-status-info/40" },
  resolved: { label: "RESOLVED", cls: "text-status-healthy border-status-healthy/40" },
};

function StatusBadge({ status }) {
  const badge = STATUS_BADGE[status];
  if (!badge) return null;
  return <span className={`text-[10px] px-1.5 py-0.5 rounded border uppercase tracking-wide ${badge.cls}`}>{badge.label}</span>;
}

function DeliveryRow({ delivery }) {
  return (
    <li className="flex items-center gap-2 text-[11px] py-0.5">
      <span className="font-mono text-ink-secondary w-20">{delivery.channel}</span>
      <span className={delivery.status === "failed" ? "text-status-critical" : "text-ink-muted"}>{delivery.status}</span>
      <span className="text-ink-muted ml-auto">{formatDate(delivery.created_at)}</span>
    </li>
  );
}

function EventRow({ event }) {
  return (
    <li className="text-[11px] py-1 border-b border-surface-border/50 last:border-0">
      <span className="text-ink-primary font-medium">{event.actor?.username}</span>
      <span className="text-ink-muted"> ({event.actor?.role}) </span>
      <span className="text-ink-secondary">
        {event.from_status} → {event.to_status}
      </span>
      <span className="text-ink-muted"> · {formatDate(event.at)}</span>
      {event.note && <div className="text-ink-muted mt-0.5">“{event.note}”</div>}
    </li>
  );
}

export default function Alerts() {
  const role = localStorage.getItem("wafguard_role");
  const canTriage = TRIAGE_ROLES.includes(role);

  const [status, setStatus] = useState("all");
  const [severity, setSeverity] = useState("all");
  const [expanded, setExpanded] = useState(null);
  const [note, setNote] = useState("");
  const [actionError, setActionError] = useState(null);
  const [acting, setActing] = useState(false);

  const list = useApi(() => {
    const params = { limit: 100 };
    if (status !== "all") params.status = status;
    if (severity !== "all") params.severity = severity;
    return get("/alerts", params);
  }, [status, severity]);

  // Per-alert detail loads lazily on expand (deliveries + append-only triage trail).
  const timeline = useApi(
    () => (expanded ? get(`/alerts/${expanded}/timeline`) : Promise.resolve({ events: [] })),
    [expanded]
  );
  const deliveries = useApi(
    () => (expanded ? get("/alerts/deliveries", { alert_id: expanded, limit: 20 }) : Promise.resolve({ deliveries: [] })),
    [expanded]
  );

  async function triage(url, body) {
    setActing(true);
    setActionError(null);
    try {
      await post(url, body);
      setNote("");
      await list.reload();
      if (expanded) {
        await Promise.all([timeline.reload(), deliveries.reload()]);
      }
    } catch (err) {
      setActionError(err.message || "Triage action failed.");
    } finally {
      setActing(false);
    }
  }

  if (list.loading) return <LoadingState label="Loading alerts…" />;
  if (list.error) return <ErrorState error={list.error} onRetry={list.reload} />;

  const alerts = list.data?.alerts || [];

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-lg font-semibold text-ink-primary">Alert Triage</h1>
          <p className="text-sm text-ink-secondary">
            Acknowledge and resolve alerts. Every transition is recorded in an append-only audit trail —
            notes describe what was checked, never a claimed lot outcome.
          </p>
        </div>
        <div className="flex gap-2 text-xs">
          <select
            aria-label="Filter by status"
            className="bg-surface-panel border border-surface-border rounded px-2 py-1.5"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
          >
            <option value="all">All statuses</option>
            <option value="open">Open</option>
            <option value="acknowledged">Acknowledged</option>
            <option value="resolved">Resolved</option>
          </select>
          <select
            aria-label="Filter by severity"
            className="bg-surface-panel border border-surface-border rounded px-2 py-1.5"
            value={severity}
            onChange={(e) => setSeverity(e.target.value)}
          >
            <option value="all">All severities</option>
            <option value="HIGH">High</option>
            <option value="MEDIUM">Medium</option>
            <option value="LOW">Low</option>
          </select>
        </div>
      </div>

      {actionError && <div className="text-xs text-status-critical border border-status-critical/30 rounded px-3 py-2">{actionError}</div>}

      {alerts.length === 0 ? (
        <EmptyState label="No alerts match this filter." />
      ) : (
        <ul className="space-y-2">
          {alerts.map((alert) => {
            const isOpen = expanded === alert.alert_id;
            return (
              <li key={alert.alert_id} className="panel p-4">
                <div className="flex items-start gap-3">
                  <span className={`w-1.5 h-1.5 rounded-full mt-2 shrink-0 ${SEVERITY_DOT[alert.severity] || SEVERITY_DOT.LOW}`} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm text-ink-primary">{alert.message}</span>
                      <StatusBadge status={alert.status} />
                    </div>
                    <div className="text-[11px] text-ink-muted mt-1 flex items-center gap-3 flex-wrap">
                      <span>{formatDate(alert.created_at)}</span>
                      {alert.lot_id && (
                        <Link to={`/lots/${alert.lot_id}`} className="text-brand hover:underline">
                          {alert.lot_id}
                        </Link>
                      )}
                      {alert.acknowledged_by && <span>acked by {alert.acknowledged_by}</span>}
                      {alert.resolved_by && <span>resolved by {alert.resolved_by}</span>}
                      <button
                        onClick={() => {
                          setExpanded(isOpen ? null : alert.alert_id);
                          setNote("");
                          setActionError(null);
                        }}
                        className="text-brand hover:underline ml-auto"
                      >
                        {isOpen ? "Hide details" : "Details"}
                      </button>
                    </div>
                  </div>
                </div>

                {isOpen && (
                  <div className="mt-3 pt-3 border-t border-surface-border grid lg:grid-cols-2 gap-4">
                    <div>
                      <div className="label-caps mb-1.5">Audit Timeline</div>
                      {timeline.loading && <p className="text-xs text-ink-muted">Loading timeline…</p>}
                      {timeline.error && <p className="text-xs text-status-critical">{timeline.error.message}</p>}
                      {timeline.data && timeline.data.events.length === 0 && (
                        <p className="text-xs text-ink-muted">No transitions yet — this alert has not been triaged.</p>
                      )}
                      {timeline.data && timeline.data.events.length > 0 && (
                        <ul>
                          {timeline.data.events.map((event, idx) => (
                            <EventRow key={idx} event={event} />
                          ))}
                        </ul>
                      )}
                      <div className="label-caps mt-3 mb-1.5">Deliveries</div>
                      {deliveries.data && deliveries.data.deliveries.length === 0 ? (
                        <p className="text-xs text-ink-muted">No delivery records for this alert.</p>
                      ) : (
                        <ul>
                          {(deliveries.data?.deliveries || []).map((delivery, idx) => (
                            <DeliveryRow key={idx} delivery={delivery} />
                          ))}
                        </ul>
                      )}
                    </div>

                    {canTriage ? (
                      <div>
                        <label className="text-xs text-ink-muted" htmlFor={`note-${alert.alert_id}`}>
                          Note (optional for acknowledge; stored in the audit trail)
                        </label>
                        <textarea
                          id={`note-${alert.alert_id}`}
                          className="mt-1 w-full bg-surface-panel border border-surface-border rounded px-2 py-1.5 text-xs"
                          rows={2}
                          value={note}
                          onChange={(e) => setNote(e.target.value)}
                          placeholder="What was checked / decided…"
                        />
                        <div className="mt-2 flex gap-2">
                          {alert.status === "open" && (
                            <button
                              disabled={acting}
                              onClick={() => triage(`/alerts/${alert.alert_id}/acknowledge`, { note: note || null })}
                              className="text-xs px-3 py-1.5 rounded-md bg-brand/15 border border-brand/40 text-brand hover:bg-brand/25 disabled:opacity-50"
                            >
                              Acknowledge
                            </button>
                          )}
                          {alert.status === "acknowledged" && (
                            <button
                              disabled={acting}
                              onClick={() => triage(`/alerts/${alert.alert_id}/resolve`, { note: note || null })}
                              className="text-xs px-3 py-1.5 rounded-md bg-status-healthy/15 border border-status-healthy/40 text-status-healthy hover:bg-status-healthy/25 disabled:opacity-50"
                            >
                              Resolve
                            </button>
                          )}
                          {alert.status === "resolved" && (
                            <span className="text-xs text-ink-muted">This alert is closed (resolved is terminal).</span>
                          )}
                        </div>
                      </div>
                    ) : (
                      <p className="text-xs text-ink-muted">
                        Your role ({role || "signed out"}) is read-only — acknowledgement and resolution are engineering actions.
                      </p>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
