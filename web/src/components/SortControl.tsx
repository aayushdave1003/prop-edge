import type { SortKey } from "../types";

const OPTIONS: { key: SortKey; label: string }[] = [
  { key: "edge", label: "Edge" },
  { key: "confidence", label: "Confidence" },
  { key: "start", label: "Start time" },
];

// Segmented sort toggle. Board defaults to Edge (best plays first).
export function SortControl({
  value,
  onChange,
}: {
  value: SortKey;
  onChange: (k: SortKey) => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="hidden text-xs font-medium uppercase tracking-wide text-ink-dim sm:inline">
        Sort
      </span>
      <div className="flex rounded-full bg-surface p-1 ring-1 ring-white/8">
        {OPTIONS.map((o) => (
          <button
            key={o.key}
            onClick={() => onChange(o.key)}
            className={[
              "rounded-full px-3 py-1 text-xs font-semibold transition",
              value === o.key
                ? "bg-surface-hover text-ink ring-1 ring-white/10"
                : "text-ink-dim hover:text-ink",
            ].join(" ")}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}
