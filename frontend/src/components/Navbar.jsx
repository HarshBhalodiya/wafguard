import { useEffect, useState } from "react";
import { get } from "../services/api";

export default function Navbar() {
  const [health, setHealth] = useState(null);

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        const data = await get("/health");
        if (!cancelled) setHealth(data);
      } catch {
        if (!cancelled) setHealth({ status: "unreachable", database: "unknown" });
      }
    }
    poll();
    const id = setInterval(poll, 15000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const statusColor =
    health === null
      ? "bg-ink-muted animate-pulse"
      : health.status === "healthy"
      ? "bg-status-healthy"
      : health.status === "degraded"
      ? "bg-status-warning"
      : "bg-status-critical";

  const statusText =
    health === null
      ? "Checking system status…"
      : health.status === "healthy"
      ? "System Healthy"
      : health.status === "degraded"
      ? "Degraded — DB disconnected"
      : "Backend unreachable";

  return (
    <header className="h-14 shrink-0 border-b border-surface-border bg-surface/80 backdrop-blur flex items-center justify-between px-6 sticky top-0 z-10">
      <div className="text-sm text-ink-secondary">
        <span className="text-ink-primary font-medium">Predict.</span> Diagnose. Prevent. Improve Yield.
      </div>
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2 text-xs text-ink-secondary">
          <span className={`w-2 h-2 rounded-full ${statusColor}`} />
          {statusText}
        </div>
        <div className="text-xs text-ink-secondary">
          {localStorage.getItem("wafguard_username") || "signed in"}
        </div>
        <div className="w-8 h-8 rounded-full bg-surface-raised border border-surface-border flex items-center justify-center text-xs font-semibold text-ink-secondary">
          {(localStorage.getItem("wafguard_username") || "PE").slice(0, 2).toUpperCase()}
        </div>
      </div>
    </header>
  );
}
