import type { SoftLine } from "../types";
import { Avatar } from "./Avatar";
import { EmptyState } from "./states";

// Market signal — INDEPENDENT of the model. Cyan-themed to make that obvious.
export function SoftLinesView({ lines, loading }: { lines: SoftLine[]; loading: boolean }) {
  return (
    <div className="space-y-4">
      <div className="rounded-[14px] border border-cyan/[0.18] bg-cyan/[0.06] p-4 text-[12.5px] leading-relaxed text-[#9FD5DF]">
        <b className="text-cyan">Soft lines · market signal.</b> Recover each book's implied
        projection from its no-vig probability, then re-price at the posted PrizePicks line. A side
        clearing the 57.7% breakeven is a “soft line.” This is a market-based edge — it does not use
        the model.
      </div>

      {loading ? (
        <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(322px, 1fr))" }}>
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-[190px] rounded-[18px] border border-hair bg-card-soft animate-pulse-soft" />
          ))}
        </div>
      ) : lines.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(322px, 1fr))" }}>
          {lines.map((l, i) => (
            <SoftCard key={i} l={l} />
          ))}
        </div>
      )}
    </div>
  );
}

function SoftCard({ l }: { l: SoftLine }) {
  const over = l.recommendation === "over";
  const consensus = l.consensus_prob; // %
  const barW = `${Math.max(0, Math.min(1, (consensus - 50) / 20)) * 100}%`;
  return (
    <div className="rounded-[18px] border border-cyan/[0.18] bg-card-soft p-4">
      <div className="flex items-center gap-3">
        <Avatar name={l.player.name} src={l.player.headshot_url} size={46} />
        <div className="min-w-0">
          <div className="truncate text-[15.5px] font-bold tracking-tight text-ink">{l.player.name}</div>
          <div className="mt-0.5 truncate text-[11px] font-semibold uppercase tracking-wide text-ink-3">
            {l.league} · {l.stat_type}
          </div>
        </div>
      </div>

      <div className="mt-4 flex gap-2.5">
        <div className="flex-1 rounded-[14px] border border-cyan/[0.25] bg-cyan/[0.10] px-4 py-3">
          <div className="microlabel">Market EV</div>
          <div className="tnum mt-1 text-[32px] font-bold leading-none tracking-tight text-cyan">
            +{l.market_ev_pct}%
          </div>
          <div className="mt-1.5 text-[10.5px] text-ink-4">no-vig consensus vs line</div>
        </div>
        <div className="flex w-[118px] shrink-0 flex-col items-center justify-center rounded-[14px] border border-hair bg-black/30 px-2 py-2.5">
          <div className="tnum text-[26px] font-bold leading-none text-ink">{l.pp_line}</div>
          <div className="microlabel mt-0.5">PP Line</div>
          <span
            className="mt-2 rounded-[9px] px-2.5 py-1 text-[12px] font-extrabold uppercase tracking-wide"
            style={{ color: over ? "#34D399" : "#F87171", background: over ? "rgba(52,211,153,0.14)" : "rgba(248,113,113,0.14)" }}
          >
            {over ? "▲ Over" : "▼ Under"}
          </span>
        </div>
      </div>

      <div className="mt-4">
        <div className="flex justify-between text-[10.5px] font-semibold uppercase tracking-wide text-ink-3">
          <span>Consensus prob</span>
          <span className="tnum text-[12px] text-cyan">{consensus}%</span>
        </div>
        <div className="relative mt-1.5 h-1.5 rounded-[5px] bg-white/[0.05]">
          <div className="h-full rounded-[5px] bg-cyan" style={{ width: barW }} />
          <div className="absolute -bottom-[3px] -top-[3px] left-[38.5%] w-[1.5px] bg-warn opacity-85" />
        </div>
        <div className="mt-1.5 text-[9.5px] text-ink-4">Median no-vig consensus across sharp books · model-independent</div>
      </div>
    </div>
  );
}
