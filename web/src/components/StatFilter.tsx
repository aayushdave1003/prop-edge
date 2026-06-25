import type { StatOption } from "../types";
import { SearchIcon } from "./icons";

// Stat-type filter row: a search button, an "All" chip, then one outline pill per
// stat. Active = white-outline treatment (outline border, transparent fill) —
// deliberately distinct from the cyan league selection.
export function StatFilter({
  stats,
  active,
  onSelect,
}: {
  stats: StatOption[];
  active: string | null;
  onSelect: (key: string | null) => void;
}) {
  return (
    <div className="flex items-center gap-2.5">
      <button
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-surface text-ink-dim ring-1 ring-white/10 transition hover:text-ink"
        aria-label="Search props"
      >
        <SearchIcon className="h-4 w-4" />
      </button>

      <div className="no-scrollbar flex items-center gap-2 overflow-x-auto">
        <Chip label="All" active={active === null} onClick={() => onSelect(null)} />
        {stats.map((s) => (
          <Chip
            key={s.key}
            label={s.label}
            active={active === s.key}
            onClick={() => onSelect(s.key)}
          />
        ))}
      </div>
    </div>
  );
}

function Chip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={[
        "shrink-0 rounded-full border px-3.5 py-1.5 text-sm font-medium transition",
        active
          ? "border-outline bg-transparent text-ink"
          : "border-transparent bg-surface text-ink-dim hover:bg-surface-hover hover:text-ink",
      ].join(" ")}
    >
      {label}
    </button>
  );
}
