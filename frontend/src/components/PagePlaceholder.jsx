export default function PagePlaceholder({ title, description }) {
  return (
    <div className="panel p-6">
      <h1 className="text-lg font-semibold text-ink-primary">{title}</h1>
      {description && <p className="text-sm text-ink-secondary mt-1">{description}</p>}
      <p className="text-xs text-ink-muted mt-4">Wired up in a later build pass.</p>
    </div>
  );
}
