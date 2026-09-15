export function LoadingState({ label = "Loading…" }) {
  return (
    <div className="panel p-8 flex items-center justify-center text-sm text-ink-secondary">
      <span className="w-3 h-3 mr-3 rounded-full border-2 border-brand border-t-transparent animate-spin" />
      {label}
    </div>
  );
}

export function ErrorState({ error, onRetry }) {
  const message = error?.message || "Something went wrong.";
  const code = error?.code;
  return (
    <div className="panel p-6 border-status-critical/30">
      <div className="flex items-start gap-3">
        <span className="w-2 h-2 mt-1.5 rounded-full bg-status-critical shrink-0" />
        <div className="flex-1">
          <div className="text-sm font-medium text-ink-primary">{message}</div>
          {code && <div className="text-xs text-ink-muted mt-1 font-mono">{code}</div>}
          {onRetry && (
            <button
              onClick={onRetry}
              className="mt-3 text-xs font-medium text-brand hover:text-brand/80 border border-brand/30 rounded px-2 py-1"
            >
              Retry
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export function EmptyState({ label = "Nothing here yet." }) {
  return <div className="panel p-8 text-center text-sm text-ink-muted">{label}</div>;
}
