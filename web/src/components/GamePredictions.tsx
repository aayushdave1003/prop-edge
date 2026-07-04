import type { Game, League } from "../types";

// Game-winner model cards: win-probability split bar (away neg-tinted / home
// accent-tinted), mono probabilities, model pick + starters.
export function GamePredictions({
  leagues,
  gamesByLeague,
  loading,
}: {
  leagues: League[];
  gamesByLeague: Record<string, Game[]>;
  loading: boolean;
}) {
  const sections = leagues.filter((l) => l.available);
  return (
    <div className="space-y-8">
      <div className="text-[13px] text-ink-2">
        <b className="text-ink">Game-winner model</b> · paper predictions
      </div>
      {sections.map((lg) => {
        const games = gamesByLeague[lg.code] ?? [];
        return (
          <section key={lg.code}>
            <div className="microlabel mb-3">{lg.label}</div>
            {loading ? (
              <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))" }}>
                {Array.from({ length: 3 }).map((_, i) => (
                  <div key={i} className="h-[120px] rounded-[18px] border border-hair bg-panel animate-pulse-soft" />
                ))}
              </div>
            ) : games.length === 0 ? (
              <div className="rounded-[18px] border border-dashed border-white/10 bg-white/[0.012] py-10 text-center text-[13px] text-ink-3">
                No {lg.label} games today.
              </div>
            ) : (
              <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))" }}>
                {games.map((g, i) => (
                  <GameCard key={i} g={g} />
                ))}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}

function GameCard({ g }: { g: Game }) {
  const homePct = Math.round(g.home_win_pct * 100);
  const awayPct = Math.round(g.away_win_pct * 100);
  return (
    <div className="rounded-[18px] border border-hair bg-panel p-[18px]">
      <div className="flex items-baseline justify-between">
        <span className="text-[14px] font-bold text-ink">
          {g.away} @ {g.home}
        </span>
        {g.implied_line && <span className="tnum text-[11px] text-ink-3">{g.implied_line}</span>}
      </div>
      <div className="mt-3.5 flex h-[34px] overflow-hidden rounded-[9px] bg-black/30">
        <div
          className="flex items-center bg-neg/[0.22] px-3 text-[12px] font-bold text-[#F0A0A0]"
          style={{ width: `${awayPct}%` }}
        >
          {awayPct}%
        </div>
        <div className="flex flex-1 items-center justify-end bg-accent-soft px-3 text-[12px] font-bold text-accent">
          {homePct}%
        </div>
      </div>
      <div className="mt-3 text-[12px] text-ink-3">
        Model pick <b className="text-ink">{g.model_pick}</b>
        {g.starters && (
          <>
            {" "}
            · SP {g.starters.away} / {g.starters.home}
          </>
        )}
      </div>
    </div>
  );
}
