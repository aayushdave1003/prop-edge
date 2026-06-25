import { useRef } from "react";
import type { League } from "../types";
import { ChevronRight, SportGlyph } from "./icons";

// Horizontally-scrollable league pills. Active = cyan-glow (brand border + soft
// outer glow + dark fill). Data-backed leagues come from /api/leagues; a few
// dimmed, non-interactive placeholders convey the sportsbook breadth (no picks,
// no numbers — nothing fabricated).
const PLACEHOLDERS = ["UFC", "Soccer", "Tennis", "PGA", "F1", "NHL"];

export function LeagueSelector({
  leagues,
  active,
  onSelect,
}: {
  leagues: League[];
  active: string | null;
  onSelect: (code: string) => void;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const activeCodes = new Set(leagues.map((l) => l.code.toUpperCase()));

  return (
    <div className="relative">
      <div
        ref={scroller}
        className="no-scrollbar flex items-center gap-2.5 overflow-x-auto pr-10"
      >
        {leagues.map((lg) => {
          const isActive = lg.code === active;
          return (
            <button
              key={lg.code}
              onClick={() => onSelect(lg.code)}
              className={[
                "flex shrink-0 items-center gap-2 rounded-full border px-3.5 py-2 text-sm font-semibold transition",
                isActive
                  ? "border-brand bg-surface text-brand shadow-glow"
                  : "border-white/8 bg-surface text-ink-dim hover:bg-surface-hover hover:text-ink",
              ].join(" ")}
            >
              <span
                className={[
                  "flex h-6 w-6 items-center justify-center rounded-full",
                  isActive ? "bg-brand/15 text-brand" : "bg-white/5 text-ink-dim",
                ].join(" ")}
              >
                <SportGlyph className="h-3.5 w-3.5" />
              </span>
              <span className="tracking-wide">{lg.label}</span>
              <span
                className={[
                  "rounded-full px-1.5 text-[11px] font-bold",
                  isActive ? "bg-brand/15 text-brand" : "bg-white/5 text-ink-dim",
                ].join(" ")}
              >
                {lg.count}
              </span>
            </button>
          );
        })}

        {/* Decorative, non-interactive placeholders for leagues without data. */}
        {PLACEHOLDERS.filter((p) => !activeCodes.has(p.toUpperCase())).map((p) => (
          <span
            key={p}
            aria-disabled
            className="flex shrink-0 cursor-not-allowed items-center gap-2 rounded-full border border-white/5 bg-surface/50 px-3.5 py-2 text-sm font-semibold text-ink-dim/40"
          >
            <span className="flex h-6 w-6 items-center justify-center rounded-full bg-white/5">
              <SportGlyph className="h-3.5 w-3.5" />
            </span>
            <span className="tracking-wide">{p}</span>
          </span>
        ))}
      </div>

      {/* right-edge fade + chevron affordance */}
      <div className="pointer-events-none absolute inset-y-0 right-0 flex w-12 items-center justify-end bg-gradient-to-l from-bg to-transparent">
        <button
          onClick={() =>
            scroller.current?.scrollBy({ left: 240, behavior: "smooth" })
          }
          className="pointer-events-auto flex h-7 w-7 items-center justify-center rounded-full bg-surface text-ink-dim ring-1 ring-white/10 hover:text-ink"
          aria-label="Scroll leagues"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
