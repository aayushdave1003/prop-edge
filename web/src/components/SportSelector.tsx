import { useRef } from "react";
import type { League } from "../types";
import { ChevronRight, SportGlyph } from "./icons";

// Horizontally-scrollable sport pills. Active = violet glow. Calendar-gated
// leagues (available:false) render disabled with a "soon" tag.
export function SportSelector({
  leagues,
  active,
  onSelect,
}: {
  leagues: League[];
  active: string | null;
  onSelect: (code: string) => void;
}) {
  const scroller = useRef<HTMLDivElement>(null);

  return (
    <div className="relative">
      <div ref={scroller} className="no-scrollbar flex items-center gap-2.5 overflow-x-auto pr-10">
        {leagues.map((lg) => {
          const isActive = lg.code === active;
          const disabled = !lg.available;
          return (
            <button
              key={lg.code}
              disabled={disabled}
              onClick={() => !disabled && onSelect(lg.code)}
              className={[
                "flex shrink-0 items-center gap-2 rounded-full border px-3.5 py-2 text-sm font-semibold transition",
                disabled
                  ? "cursor-not-allowed border-white/5 bg-surface/50 text-ink-dim/40"
                  : isActive
                    ? "border-violet bg-violet/10 text-ink shadow-violet-glow"
                    : "border-white/8 bg-surface text-ink-dim hover:bg-surface-hover hover:text-ink",
              ].join(" ")}
            >
              <span
                className={[
                  "flex h-6 w-6 items-center justify-center rounded-full",
                  isActive && !disabled ? "bg-violet/20 text-violet" : "bg-white/5",
                ].join(" ")}
              >
                <SportGlyph className="h-3.5 w-3.5" />
              </span>
              <span className="tracking-wide">{lg.label}</span>
              {disabled ? (
                <span className="rounded-full bg-white/5 px-1.5 text-[10px] font-bold uppercase text-ink-dim/60">
                  soon
                </span>
              ) : (
                <span
                  className={[
                    "rounded-full px-1.5 text-[11px] font-bold",
                    isActive ? "bg-violet/20 text-violet" : "bg-white/5 text-ink-dim",
                  ].join(" ")}
                >
                  {lg.count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      <div className="pointer-events-none absolute inset-y-0 right-0 flex w-12 items-center justify-end bg-gradient-to-l from-bg to-transparent">
        <button
          onClick={() => scroller.current?.scrollBy({ left: 240, behavior: "smooth" })}
          className="pointer-events-auto flex h-7 w-7 items-center justify-center rounded-full bg-surface text-ink-dim ring-1 ring-white/10 hover:text-ink"
          aria-label="Scroll sports"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
