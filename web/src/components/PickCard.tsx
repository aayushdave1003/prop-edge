import type { FormResult, Pick } from "../types";
import { Avatar } from "./Avatar";
import { Star, WindIcon } from "./icons";

// The card: keeps EVERY model field the Streamlit dashboard shows. OVER/UNDER are
// model leans (research), not bets. All numbers come straight from the API.

// faint, deterministic "team-color" gradient from the team abbreviation
function teamHue(team: string): number {
  let h = 0;
  for (let i = 0; i < team.length; i++) h = (h * 31 + team.charCodeAt(i)) >>> 0;
  return h % 360;
}

function FormSquares({ form }: { form: FormResult[] }) {
  // contract is recent-first; the card shows OLD → RECENT, so reverse for display
  const oldToRecent = [...form].reverse();
  return (
    <div className="flex items-center gap-1">
      {oldToRecent.map((f, i) => (
        <span
          key={i}
          className={[
            "flex h-4 w-4 items-center justify-center rounded-[4px] text-[9px] font-bold",
            f === null
              ? "bg-white/10 text-ink-dim"
              : f
                ? "bg-mint/20 text-mint"
                : "bg-coral/20 text-coral",
          ].join(" ")}
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
  watched,
  onToggleWatch,
}: {
  pick: Pick;
  watched: boolean;
  onToggleWatch: () => void;
}) {
  const over = pick.recommendation === "over";
  const leanColor = over ? "text-mint" : "text-coral";
  const hue = teamHue(pick.player.team || pick.player.name);
  const kellyBar = Math.min(100, Math.max(0, pick.kelly_pct * 5)); // relative indicator

  return (
    <div className="relative flex flex-col overflow-hidden rounded-2xl border border-white/5 bg-surface transition hover:border-white/10 hover:bg-surface-hover">
      {/* faint team-color gradient */}
      <div
        className="pointer-events-none absolute inset-x-0 top-0 h-20 opacity-25"
        style={{
          background: `linear-gradient(180deg, hsl(${hue} 70% 45% / 0.55) 0%, transparent 100%)`,
        }}
      />

      <div className="relative flex flex-col p-4">
        {/* header */}
        <div className="flex items-start gap-3">
          <Avatar name={pick.player.name} src={pick.player.headshot_url} />
          <div className="min-w-0 flex-1">
            <div className="flex items-start justify-between gap-2">
              <div className="truncate text-[15px] font-bold leading-tight text-ink">
                {pick.player.name}
              </div>
              <div className="flex items-center gap-2">
                {pick.player.team_logo_url ? (
                  <img src={pick.player.team_logo_url} alt={pick.player.team} className="h-5 w-5 object-contain" />
                ) : (
                  <span className="rounded bg-white/8 px-1.5 py-0.5 text-[10px] font-bold text-ink-dim">
                    {pick.player.team}
                  </span>
                )}
                <button
                  onClick={onToggleWatch}
                  aria-label={watched ? "Remove from watchlist" : "Add to watchlist"}
                  className={watched ? "text-violet" : "text-ink-dim hover:text-ink"}
                >
                  <Star className="h-4 w-4" filled={watched} />
                </button>
              </div>
            </div>
            <div className="mt-0.5 truncate text-[11px] font-semibold uppercase tracking-wide text-ink-dim">
              {pick.player.team} · {pick.matchup} · {pick.stat_type}
            </div>
          </div>
        </div>

        {/* weather chip */}
        {pick.weather && (
          <div className="mt-3 inline-flex w-fit items-center gap-1.5 rounded-full border border-mint/40 px-2.5 py-1 text-[11px] font-semibold text-mint">
            {pick.weather.temp_f != null && <span>{pick.weather.temp_f}°F ·</span>}
            <WindIcon className="h-3.5 w-3.5" />
            {pick.weather.note}
          </div>
        )}

        {/* line + lean */}
        <div className="mt-3 flex items-center justify-center gap-4 rounded-xl bg-bg-deep/60 py-3">
          <div className="flex flex-col items-center">
            <span className="text-3xl font-extrabold leading-none tracking-tight text-ink">
              {pick.line}
            </span>
            <span className="mt-1 text-[10px] font-semibold uppercase tracking-wide text-ink-dim">
              Line
            </span>
          </div>
          <span
            className={[
              "rounded-full px-3.5 py-2 text-sm font-extrabold uppercase tracking-wide",
              over ? "bg-mint/15 text-mint shadow-mint-glow" : "bg-coral/15 text-coral",
            ].join(" ")}
          >
            {over ? "▲ Over" : "▼ Under"}
          </span>
        </div>

        {/* projection + likely range */}
        <div className="mt-3 text-center text-sm text-ink-dim">
          📈 Projection <b className={leanColor}>{pick.model_projection}</b> · likely{" "}
          <b className="text-ink">{pick.likely_range}</b>
        </div>

        {/* model confidence */}
        <div className="mt-3 flex items-center justify-between text-sm">
          <span className="text-ink-dim">Model confidence</span>
          <span className="font-bold text-ink">{pick.model_confidence}%</span>
        </div>

        {/* paper Kelly + progress bar */}
        <div className="mt-2">
          <div className="flex items-center justify-between text-sm">
            <span className="text-ink-dim">
              Kelly sizing <span className="text-[10px] uppercase text-ink-dim/70">· paper</span>
            </span>
            <span className="font-bold text-violet">
              {pick.kelly_pct > 0 ? `Kelly ${pick.kelly_pct}%` : "—"}
            </span>
          </div>
          <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-white/8">
            <div
              className="h-full rounded-full bg-gradient-to-r from-violet to-brand-cyan"
              style={{ width: `${kellyBar}%` }}
            />
          </div>
        </div>

        {/* form */}
        <div className="mt-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-semibold uppercase tracking-wide text-ink-dim">
              Form · old → recent
            </span>
            <FormSquares form={pick.form} />
          </div>
          <span className="text-[11px] font-semibold text-ink-dim">
            L5 {pick.l5} · L10 {pick.l10}
          </span>
        </div>

        {/* insight */}
        {pick.insight && (
          <div className="mt-3 rounded-lg bg-violet/5 px-2.5 py-1.5 text-[11px] text-ink-dim">
            💡 {pick.insight}
          </div>
        )}
      </div>
    </div>
  );
}
