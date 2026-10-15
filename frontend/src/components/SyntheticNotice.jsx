/** Phase 10: the honesty banner every decision screen carries. */
export default function SyntheticNotice({ children }) {
  return (
    <div className="rounded-md border border-status-warning/30 bg-status-warning/10 px-3 py-2 text-xs text-status-warning">
      <span className="font-semibold uppercase tracking-wide mr-1">Synthetic data.</span>
      {children ||
        "Every number on this screen comes from simulated lots and models trained on them — not from a real fab."}
    </div>
  );
}
