// Small inline SVG icons (no icon library). All inherit currentColor.
type P = { className?: string };

export function SearchIcon({ className }: P) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.2-3.2" strokeLinecap="round" />
    </svg>
  );
}

export function ChevronRight({ className }: P) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5}>
      <path d="m9 6 6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function ChevronDown({ className }: P) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5}>
      <path d="m6 9 6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function PlusIcon({ className }: P) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5}>
      <path d="M12 5v14M5 12h14" strokeLinecap="round" />
    </svg>
  );
}

export function ChatIcon({ className }: P) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path
        d="M21 12a8 8 0 0 1-11.5 7.2L4 20l1-4.2A8 8 0 1 1 21 12Z"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// Generic sport glyph used inside league chips (decorative).
export function SportGlyph({ className }: P) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}>
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18" />
    </svg>
  );
}

// prop-edge logo mark — an upward "edge" chevron stack in the brand color.
export function LogoMark({ className }: P) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none">
      <path
        d="M4 15.5 12 8l8 7.5"
        stroke="currentColor"
        strokeWidth={2.6}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M4 10.5 12 3l8 7.5"
        stroke="currentColor"
        strokeWidth={2.6}
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity={0.45}
      />
    </svg>
  );
}
