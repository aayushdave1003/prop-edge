import type { League } from "../types";

// Horizontal league pills. Active = accent border + accent-soft fill. Unavailable
// (calendar-gated) leagues are dimmed with a mono "soon" tag.
export function SportSelector({
  leagues,
  active,
  onSelect,
  onUnavailable,
}: {
  leagues: League[];
  active: string | null;
  onSelect: (code: string) => void;
  onUnavailable: (label: string) => void;
}) {
  return (
    <div className="no-scrollbar flex items-center gap-2 overflow-x-auto pb-0.5">
      {leagues.map((lg) => {
        const isActive = lg.code === active;
        const disabled = !lg.available;
        return (
          <button
            key={lg.code}
            onClick={() => (disabled ? onUnavailable(lg.label) : onSelect(lg.code))}
            className={[
              "flex shrink-0 items-center gap-2 whitespace-nowrap rounded-[10px] border px-3.5 py-2 text-[13px] font-semibold transition",
              disabled
                ? "cursor-not-allowed border-hair bg-transparent text-ink-3 opacity-55"
                : isActive
                  ? "border-accent-border bg-accent-soft text-ink"
                  : "border-hair bg-white/[0.02] text-ink-3 hover:text-ink",
            ].join(" ")}
          >
            {lg.label}
            {disabled ? (
              <span className="tnum text-[10px] uppercase text-ink-4">soon</span>
            ) : (
              <span className={["tnum text-[12px]", isActive ? "text-accent" : "text-ink-4"].join(" ")}>
                {lg.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
