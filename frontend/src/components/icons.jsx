/**
 * Inline SVG status icons.
 *
 * These used to be pictographic characters (a "⚠" warning sign, a "✕" close
 * glyph). Those are at the mercy of the platform font: on some systems they
 * render as full-colour emoji, on others as a differently-sized glyph, and they
 * never match the surrounding stroke weight. Everything here is a plain stroke
 * that inherits `currentColor`, so an icon always matches the text it sits
 * next to and scales with the size class.
 */

export function WarningIcon({ className = "w-3.5 h-3.5" }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
      <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z" />
      <path d="M12 9v4" />
      <path d="M12 17h.01" />
    </svg>
  );
}

export function CloseIcon({ className = "w-3 h-3" }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" className={className} aria-hidden="true">
      <path d="M6 6l12 12" />
      <path d="M18 6 6 18" />
    </svg>
  );
}
