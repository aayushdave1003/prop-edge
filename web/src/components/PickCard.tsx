import type { FormResult, Pick } from "../types";
import { Avatar } from "./Avatar";
import { BurstStar, WindIcon } from "./icons";

// ── gauge geometry ──────────────────────────────────────────────────────────
// Map the line / projection / likely-range onto a 0–100% track so the card
// literally shows where the model lands relative to the posted line.
function parseRange(r: string): [number, number] | null {
  const m = r.match(/(-?\d+(?:\.\d+)?)\s*[–-]\s*(-?\d+(?:\.\d+)?)/);
  return m ? [parseFloat(m[1]), parseFloat(m[2])] : null;
}
function computeGauge(line: number, proj: number, likely: string) {
  const band = parseRange(likely);
  const vals = [line, proj, ...(band ?? [])];
  let lo = Math.min(...vals);
  let hi = Math.max(...vals);
  let span = hi - lo || 1;
  const pad = span * 0.22;
  lo -= pad;
  hi += pad;
  span = hi - lo;
  const pct = (v: number) => `${((v - lo) / span) * 100}%`;
  return {
    linePct: pct(line),
    projPct: pct(proj),
    bandLeft: band ? pct(band[0]) : "0%",
    bandWidth: band ? `${((band[1] - band[0]) / span) * 100}%` : "0%",
    hasBand: !!band,
  };
}

const CONF_BAR = (c: number) => `${Math.max(0, Math.min(1, (c - 50) / 20)) * 100}%`;

function FormCells({ form }: { form: FormResult[] }) {
  const cells = [...form].reverse(); // contract is recent-first; show old → recent
  return (
    <div className="flex gap-1">
      {cells.map((f, i) => (
        <span
          key={i}
          className="flex h-[17px] w-[17px] items-center justify-center rounded-[5px] text-[10px] font-bold"
          style={{
            background: f === null ? "rgba(255,255,255,0.06)" : f ? "rgba(52,211,153,0.16)" : "rgba(248,113,113,0.16)",
            color: f === null ? "#6E6E7C" : f ? "#34D399" : "#F87171",
          }}
          title={f === null ? "push" : f ? "hit" : "miss"}
        >
          {f === null ? "•" : f ? "✓" : "✗"}
        </span>
      ))}
    </div>
  );
}

export function PickCard({
  pick,
  rank,
  watched,
  onToggleWatch,
}: {
  pick: Pick;
  rank: number;
  watched: boolean;
  onToggleWatch: () => void;
}) {
  const over = pick.recommendation === "over";
  const rec = pick.recommended;
  const leanColor = over ? "#34D399" : "#F87171";
  const posEdge = pick.edge_pct >= 0;
  const edgeColor = posEdge ? "#34D399" : "#F87171";
  const edgeGlow = posEdge ? "rgba(52,211,153,0.10)" : "rgba(248,113,113,0.10)";
  const edgeBorder = posEdge ? "rgba(52,211,153,0.25)" : "rgba(248,113,113,0.25)";
  const cardBg = rec ? "#12101D" : "#0E0E16";
  const g = computeGauge(pick.line, pick.model_projection, pick.likely_range);
  const kellyBar = `${Math.min(100, Math.max(0, pick.kelly_pct * 5))}%`;

  return (
    <div
      className="relative overflow-hidden rounded-[18px] p-4 transition-colors"
      style={{
        background: cardBg,
        border: `1px solid ${rec ? "rgba(124,92,255,0.42)" : "rgba(255,255,255,0.06)"}`,
        boxShadow: rec ? "0 0 30px rgba(124,92,255,0.07)" : "none",
      }}
    >
      {/* top gradient rule (lean-colored) */}
      <div
        className="pointer-events-none absolute inset-x-0 top-0 h-0.5"
        style={{ background: `linear-gradient(90deg, transparent, ${leanColor}, transparent)`, opacity: 0.55 }}
      />

      {/* rank + edge badge */}
      <div className="absolute right-4 top-3.5 flex items-center gap-2">
        {rec && (
          <span className="flex items-center gap-1 rounded-md border border-accent-border bg-accent-soft px-1.5 py-[3px] text-[9.5px] font-bold uppercase tracking-wide text-accent">
            <BurstStar className="h-2.5 w-2.5" filled />
            Edge
          </span>
        )}
        <span className="tnum text-[12px] text-ink-5">#{String(rank).padStart(2, "0")}</span>
      </div>

      {/* header */}
      <div className="flex items-center gap-3 pr-16">
        <Avatar name={pick.player.name} src={pick.player.headshot_url} size={46} />
        <div className="min-w-0">
          <div className="truncate text-[15.5px] font-bold tracking-tight text-ink">{pick.player.name}</div>
          <div className="mt-0.5 truncate text-[11px] font-semibold uppercase tracking-wide text-ink-3">
            {pick.player.team} · {pick.matchup} · {pick.stat_type}
          </div>
        </div>
      </div>

      {/* watch star — larger hit area on mobile (tap-target), icon stays small */}
      <button
        onClick={onToggleWatch}
        aria-label={watched ? "Unwatch" : "Watch"}
        className="tap-target absolute bottom-[9px] right-[9px] flex items-center justify-center p-1.5 sm:bottom-4 sm:right-4 sm:p-0"
        style={{ color: watched ? "#7C5CFF" : "#787886" }}
      >
        <BurstStar className="h-[17px] w-[17px]" filled={watched} />
      </button>

      {/* weather */}
      {pick.weather && (
        <div className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-cyan/[0.18] bg-cyan/[0.08] px-2.5 py-1 text-[11px] font-semibold text-[#5FB8C9]">
          {pick.weather.temp_f != null && <span>{pick.weather.temp_f}°F ·</span>}
          <WindIcon className="h-3 w-3" />
          {pick.weather.note}
        </div>
      )}

      {/* EDGE HERO */}
      <div className="mt-4 flex gap-2.5">
        <div className="flex-1 rounded-[14px] px-4 py-3" style={{ background: edgeGlow, border: `1px solid ${edgeBorder}` }}>
          <div className="microlabel">Model Edge</div>
          <div className="tnum mt-1 text-[32px] font-bold leading-none tracking-tight" style={{ color: edgeColor }}>
            {posEdge ? "+" : ""}
            {pick.edge_pct}%
          </div>
          <div className="mt-1.5 text-[10.5px] text-ink-4">projection vs posted line</div>
        </div>
        <div className="flex w-[118px] shrink-0 flex-col items-center justify-center rounded-[14px] border border-hair bg-black/30 px-2 py-2.5">
          <div className="tnum text-[26px] font-bold leading-none text-ink">{pick.line}</div>
          <div className="microlabel mt-0.5">Line</div>
          <span
            className="mt-2 rounded-[9px] px-2.5 py-1 text-[12px] font-extrabold uppercase tracking-wide"
            style={{ color: leanColor, background: over ? "rgba(52,211,153,0.14)" : "rgba(248,113,113,0.14)" }}
          >
            {over ? "▲ Over" : "▼ Under"}
          </span>
        </div>
      </div>

      {/* projection gauge */}
      <div className="mt-4">
        <div className="flex justify-between gap-2 text-[10.5px] font-semibold uppercase tracking-wide text-ink-3">
          <span>
            Projection <b className="tnum" style={{ color: edgeColor }}>{pick.model_projection}</b>
          </span>
          <span>
            Likely <b className="tnum text-ink-2">{pick.likely_range}</b>
          </span>
        </div>
        <div className="relative mt-2 h-[9px] rounded-md bg-white/[0.05]">
          {g.hasBand && (
            <div className="absolute inset-y-0 rounded-md bg-accent-soft" style={{ left: g.bandLeft, width: g.bandWidth }} />
          )}
          <div className="absolute -bottom-[3px] -top-[3px] w-0.5 -translate-x-1/2 bg-ink-2" style={{ left: g.linePct }} />
          <div
            className="absolute top-1/2 h-[13px] w-[13px] -translate-x-1/2 -translate-y-1/2 rounded-full"
            style={{ left: g.projPct, background: edgeColor, boxShadow: `0 0 0 3px ${cardBg}, 0 0 10px ${edgeGlow}` }}
          />
        </div>
        <div className="mt-1.5 flex justify-between text-[9.5px] text-ink-4">
          <span>◦ line {pick.line}</span>
          <span style={{ color: edgeColor }}>● model</span>
        </div>
      </div>

      {/* confidence */}
      <div className="mt-3.5">
        <div className="flex justify-between text-[10.5px] font-semibold uppercase tracking-wide text-ink-3">
          <span>Model confidence</span>
          <span className="tnum text-[12px] text-ink">{pick.model_confidence}%</span>
        </div>
        <div className="relative mt-1.5 h-1.5 rounded-[5px] bg-white/[0.05]">
          <div className="h-full rounded-[5px] bg-brand" style={{ width: CONF_BAR(pick.model_confidence) }} />
          <div className="absolute -bottom-[3px] -top-[3px] left-[38.5%] w-[1.5px] bg-warn opacity-85" title="breakeven" />
        </div>
        <div className="mt-1 text-right text-[9.5px] text-ink-4">breakeven 57.7%</div>
      </div>

      {/* kelly */}
      <div className="mt-3">
        <div className="flex justify-between text-[10.5px] font-semibold uppercase tracking-wide text-ink-3">
          <span>Kelly · paper</span>
          <span className="tnum text-[12px] text-accent">{pick.kelly_pct > 0 ? `${pick.kelly_pct}%` : "—"}</span>
        </div>
        <div className="mt-1.5 h-[5px] rounded bg-white/[0.05]">
          <div className="h-full rounded bg-accent opacity-85" style={{ width: kellyBar }} />
        </div>
      </div>

      {/* form */}
      <div className="mt-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-[9.5px] font-semibold uppercase tracking-wide text-ink-4">Form</span>
          <FormCells form={pick.form} />
        </div>
        <span className="tnum text-[11px] font-semibold text-ink-3">
          L5 {pick.l5} · L10 {pick.l10}
        </span>
      </div>

      {/* insight */}
      {pick.insight && (
        <div className="mt-3 rounded-[10px] border border-accent/[0.12] bg-accent/[0.06] px-3 py-2 text-[11.5px] text-ink-2" style={{ borderLeft: "2px solid #7C5CFF" }}>
          {pick.insight}
        </div>
      )}
    </div>
  );
}
