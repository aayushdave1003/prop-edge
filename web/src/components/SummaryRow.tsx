import type { Summary } from "../types";

// 5 KPI cards: colored 3px left accent bar, uppercase label, big mono value, sub.
export function SummaryRow({ summary }: { summary: Summary | null }) {
  const s = summary;
  const edge = s ? s.avg_edge_pct : 0;
  const cards = [
    { label: "Today's Board", value: s ? `${s.today}` : "—", sub: "picks scored", bar: "#7A7A88", color: "#ECECF2" },
    { label: "Edge Picks", value: s ? `${s.recommended}` : "—", sub: "clear the cutoff", bar: "#7C5CFF", color: "#7C5CFF" },
    {
      label: "Avg Edge",
      value: s ? `${edge >= 0 ? "+" : ""}${edge}%` : "—",
      sub: "projection vs line",
      bar: edge >= 0 ? "#34D399" : "#F87171",
      color: edge >= 0 ? "#34D399" : "#F87171",
    },
    {
      label: "7-Day Record",
      value: s ? `${s.w}–${s.l}` : "—",
      sub: s ? `${s.win_rate_pct}% hit · logged` : "logged picks",
      bar: "#7A7A88",
      color: "#ECECF2",
    },
    { label: "Breakeven", value: "57.7%", sub: "2-leg parlay", bar: "#F5B544", color: "#F5B544" },
  ];
  return (
    <div className="grid grid-cols-2 gap-3.5 sm:grid-cols-3 lg:grid-cols-5">
      {cards.map((c) => (
        <div
          key={c.label}
          className="relative overflow-hidden rounded-[14px] border border-hair bg-panel px-4 py-3.5"
        >
          <span className="absolute inset-y-0 left-0 w-[3px]" style={{ background: c.bar }} />
          <div className="microlabel">{c.label}</div>
          <div className="tnum mt-1.5 text-[26px] font-bold leading-none tracking-tight" style={{ color: c.color }}>
            {c.value}
          </div>
          <div className="mt-1.5 text-[10.5px] text-ink-4">{c.sub}</div>
        </div>
      ))}
    </div>
  );
}
