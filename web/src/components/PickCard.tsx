import type { Confidence, Pick } from "../types";
import { Avatar } from "./Avatar";

// The card that makes prop-edge different from PrizePicks: it shows the PP line,
// the model's projection, the signed edge, a confidence tier, and pre-selects the
// model-favored More/Less side. Every value is rendered straight from the API.

function formatStart(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { weekday: "short", hour: "numeric", minute: "2-digit" });
}

const CONF_BARS: Record<Confidence, number> = { low: 1, med: 2, high: 3 };
const CONF_LABEL: Record<Confidence, string> = { low: "Low", med: "Med", high: "High" };

function ConfidenceMeter({ level }: { level: Confidence }) {
  const filled = CONF_BARS[level];
  return (
    <div className="flex items-center gap-1.5" title={`Model confidence: ${CONF_LABEL[level]}`}>
      <div className="flex items-end gap-0.5">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className={[
              "w-1 rounded-sm",
              i === 0 ? "h-2" : i === 1 ? "h-3" : "h-4",
              i < filled ? "bg-ink" : "bg-white/15",
            ].join(" ")}
          />
        ))}
      </div>
      <span className="text-[11px] font-semibold uppercase tracking-wide text-ink-dim">
        {CONF_LABEL[level]}
      </span>
    </div>
  );
}

export function PickCard({ pick }: { pick: Pick }) {
  const positive = pick.edge_pct >= 0;
  const recMore = pick.recommendation === "more";
  const edgeColor = positive ? "text-edge" : "text-coral";

  return (
    <div className="group flex flex-col rounded-2xl border border-white/5 bg-surface p-4 transition hover:border-white/10 hover:bg-surface-hover">
      {/* header: player identity + matchup */}
      <div className="flex items-center gap-3">
        <Avatar name={pick.player.name} src={pick.player.headshot_url} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[15px] font-bold leading-tight text-ink">
            {pick.player.name}
          </div>
          <div className="mt-0.5 flex items-center gap-1.5 text-xs text-ink-dim">
            <span className="font-semibold text-ink-dim">{pick.player.team}</span>
            <span className="opacity-40">·</span>
            <span className="truncate">{pick.matchup}</span>
          </div>
          <div className="text-[11px] text-ink-dim/70">{formatStart(pick.start_time)}</div>
        </div>
      </div>

      {/* center: stat + PP line (hero) next to the model projection */}
      <div className="mt-4 rounded-xl bg-bg-deep/60 px-3 py-3">
        <div className="text-center text-[11px] font-bold uppercase tracking-[0.12em] text-ink-dim">
          {pick.stat_type}
        </div>
        <div className="mt-1 flex items-stretch justify-center gap-4">
          <div className="flex flex-col items-center justify-center">
            <span className="text-3xl font-extrabold leading-none tracking-tight text-ink">
              {pick.pp_line}
            </span>
            <span className="mt-1 text-[10px] font-semibold uppercase tracking-wide text-ink-dim">
              PP Line
            </span>
          </div>
          <div className="w-px self-stretch bg-white/10" />
          <div className="flex flex-col items-center justify-center">
            <span className={`text-3xl font-extrabold leading-none tracking-tight ${edgeColor}`}>
              {pick.model_projection}
            </span>
            <span className="mt-1 text-[10px] font-semibold uppercase tracking-wide text-ink-dim">
              Projection
            </span>
          </div>
        </div>
      </div>

      {/* edge + confidence */}
      <div className="mt-3 flex items-center justify-between">
        <div className="flex items-baseline gap-1.5">
          <span className="text-[11px] font-semibold uppercase tracking-wide text-ink-dim">
            Edge
          </span>
          <span className={`text-base font-extrabold ${edgeColor}`}>
            {positive ? "+" : ""}
            {pick.edge_pct}%
          </span>
        </div>
        <ConfidenceMeter level={pick.confidence} />
      </div>

      {/* recommendation: model-favored side filled green, the other outlined */}
      <div className="mt-4 grid grid-cols-2 gap-2">
        <RecSide label="More" active={recMore} />
        <RecSide label="Less" active={!recMore} />
      </div>
    </div>
  );
}

function RecSide({ label, active }: { label: string; active: boolean }) {
  return (
    <div
      className={[
        "flex items-center justify-center gap-1 rounded-full py-2 text-sm font-bold uppercase tracking-wide transition",
        active
          ? "bg-edge text-black shadow-edge-glow"
          : "border border-white/15 text-ink-dim",
      ].join(" ")}
    >
      {active && <span aria-hidden>{label === "More" ? "▲" : "▼"}</span>}
      {label}
    </div>
  );
}
