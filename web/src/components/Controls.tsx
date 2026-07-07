import type { Direction, SortKey, StatOption } from "../types";
import { BurstStar } from "./icons";

// Controls panel: market chips (multi-select, left) + direction segmented, an
// "Edge picks only" star toggle, and a sort segmented control (right).
export function Controls({
  stats,
  selected,
  onToggleStat,
  onClear,
  direction,
  onDirection,
  recommendedOnly,
  onRecommendedOnly,
  sort,
  onSort,
}: {
  stats: StatOption[];
  selected: string[];
  onToggleStat: (k: string) => void;
  onClear: () => void;
  direction: Direction;
  onDirection: (d: Direction) => void;
  recommendedOnly: boolean;
  onRecommendedOnly: (v: boolean) => void;
  sort: SortKey;
  onSort: (s: SortKey) => void;
}) {
  return (
    <div className="flex flex-col gap-3 rounded-[14px] border border-hair bg-panel p-3 lg:flex-row lg:items-center lg:justify-between">
      {/* market chips */}
      <div className="no-scrollbar flex min-w-0 items-center gap-2 overflow-x-auto">
        <Chip label="All markets" active={selected.length === 0} onClick={onClear} />
        {stats.map((s) => (
          <Chip
            key={s.key}
            label={s.label}
            count={s.count}
            active={selected.includes(s.key)}
            onClick={() => onToggleStat(s.key)}
          />
        ))}
      </div>

      {/* direction + edge-only + sort */}
      <div className="flex shrink-0 items-center gap-2.5">
        <Segmented
          value={direction}
          onChange={(v) => onDirection(v as Direction)}
          options={[
            { key: "both", label: "Both" },
            { key: "over", label: "Over" },
            { key: "under", label: "Under" },
          ]}
        />
        <button
          onClick={() => onRecommendedOnly(!recommendedOnly)}
          className={[
            "tap-target flex items-center justify-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[12px] font-semibold transition",
            recommendedOnly
              ? "border-accent-border bg-accent-soft text-accent"
              : "border-hair text-ink-3 hover:text-ink",
          ].join(" ")}
        >
          <BurstStar className="h-3.5 w-3.5" filled={recommendedOnly} />
          Edge only
        </button>
        <Segmented
          value={sort}
          onChange={(v) => onSort(v as SortKey)}
          options={[
            { key: "edge", label: "Edge" },
            { key: "confidence", label: "Conf" },
            { key: "start", label: "Time" },
          ]}
        />
      </div>
    </div>
  );
}

function Chip({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count?: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={[
        "tap-target flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg border px-3 py-1.5 text-[12.5px] font-semibold transition",
        active
          ? "border-accent-border bg-accent-soft text-accent"
          : "border-hair bg-white/[0.02] text-ink-3 hover:text-ink",
      ].join(" ")}
    >
      {label}
      {count != null && <span className="tnum text-[11px] opacity-60">{count}</span>}
    </button>
  );
}

function Segmented({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { key: string; label: string }[];
}) {
  return (
    <div className="flex rounded-lg border border-hair bg-white/[0.02] p-0.5">
      {options.map((o) => (
        <button
          key={o.key}
          onClick={() => onChange(o.key)}
          className={[
            "tap-target inline-flex items-center justify-center rounded-[7px] px-2.5 py-1 text-[12px] font-semibold transition",
            value === o.key ? "bg-accent text-[#0A0A11]" : "text-ink-3 hover:text-ink",
          ].join(" ")}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
