import type { Direction, StatOption } from "../types";
import { SearchIcon } from "./icons";

// Stat-type chips (multi-select) + Direction toggle (Over/Under/Both) + a
// Recommended-only switch. Active stat chip = violet-tinted fill.
export function StatFilter({
  stats,
  selected,
  onToggleStat,
  onClear,
  direction,
  onDirection,
  recommendedOnly,
  onRecommendedOnly,
}: {
  stats: StatOption[];
  selected: string[];
  onToggleStat: (key: string) => void;
  onClear: () => void;
  direction: Direction;
  onDirection: (d: Direction) => void;
  recommendedOnly: boolean;
  onRecommendedOnly: (v: boolean) => void;
}) {
  return (
    <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
      {/* stat chips */}
      <div className="flex min-w-0 items-center gap-2.5">
        <button
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-surface text-ink-dim ring-1 ring-white/10 transition hover:text-ink"
          aria-label="Search props"
        >
          <SearchIcon className="h-4 w-4" />
        </button>
        <div className="no-scrollbar flex items-center gap-2 overflow-x-auto">
          <Chip label="All" active={selected.length === 0} onClick={onClear} />
          {stats.map((s) => (
            <Chip
              key={s.key}
              label={s.label}
              active={selected.includes(s.key)}
              onClick={() => onToggleStat(s.key)}
            />
          ))}
        </div>
      </div>

      {/* direction + recommended-only */}
      <div className="flex shrink-0 items-center gap-3">
        <div className="flex rounded-full bg-surface p-1 ring-1 ring-white/8">
          {(["over", "under", "both"] as Direction[]).map((d) => (
            <button
              key={d}
              onClick={() => onDirection(d)}
              className={[
                "rounded-full px-3 py-1 text-xs font-bold uppercase tracking-wide transition",
                direction === d
                  ? d === "over"
                    ? "bg-mint/15 text-mint"
                    : d === "under"
                      ? "bg-coral/15 text-coral"
                      : "bg-violet/20 text-violet"
                  : "text-ink-dim hover:text-ink",
              ].join(" ")}
            >
              {d}
            </button>
          ))}
        </div>

        <button
          onClick={() => onRecommendedOnly(!recommendedOnly)}
          className="flex items-center gap-2 text-xs font-semibold text-ink-dim"
        >
          <span
            className={[
              "relative h-5 w-9 rounded-full transition",
              recommendedOnly ? "bg-violet" : "bg-white/10",
            ].join(" ")}
          >
            <span
              className={[
                "absolute top-0.5 h-4 w-4 rounded-full bg-white transition",
                recommendedOnly ? "left-[18px]" : "left-0.5",
              ].join(" ")}
            />
          </span>
          <span className={recommendedOnly ? "text-ink" : ""}>Recommended only</span>
        </button>
      </div>
    </div>
  );
}

function Chip({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={[
        "shrink-0 rounded-full border px-3.5 py-1.5 text-sm font-medium transition",
        active
          ? "border-violet/60 bg-violet/15 text-ink"
          : "border-transparent bg-surface text-ink-dim hover:bg-surface-hover hover:text-ink",
      ].join(" ")}
    >
      {label}
    </button>
  );
}
