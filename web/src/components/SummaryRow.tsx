import type { Summary } from "../types";
import { InfoIcon } from "./icons";

// 5 stat cards with a violet left-accent. Big number, small label.
export function SummaryRow({ summary }: { summary: Summary | null }) {
  const cards = [
    { label: "Today's Picks", value: summary ? `${summary.today}` : "—" },
    { label: "Recommended", value: summary ? `${summary.recommended}` : "—", tip: "Picks that clear their per-category confidence cutoff (paper-tracked)." },
    { label: "Avg Edge", value: summary ? `${summary.avg_edge_pct}%` : "—" },
    { label: "7-Day W/L", value: summary ? `${summary.w}W – ${summary.l}L` : "—" },
    { label: "7-Day Win Rate", value: summary ? `${summary.win_rate_pct}%` : "—" },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      {cards.map((c) => (
        <div
          key={c.label}
          className="relative overflow-hidden rounded-2xl border border-white/5 bg-surface px-4 py-3"
        >
          <span className="absolute inset-y-0 left-0 w-1 bg-violet" />
          <div className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wide text-ink-dim">
            {c.label}
            {c.tip && (
              <span className="group relative">
                <InfoIcon className="h-3 w-3 cursor-help opacity-70" />
                <span className="pointer-events-none absolute left-1/2 top-5 z-10 hidden w-48 -translate-x-1/2 rounded-lg bg-bg-deep p-2 text-[11px] font-normal normal-case text-ink-dim ring-1 ring-white/10 group-hover:block">
                  {c.tip}
                </span>
              </span>
            )}
          </div>
          <div className="mt-1 text-2xl font-extrabold tracking-tight text-ink">{c.value}</div>
        </div>
      ))}
    </div>
  );
}
