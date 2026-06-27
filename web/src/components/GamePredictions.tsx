import type { Game, League } from "../types";

// Game Predictions tab: per-league sections with model win probabilities, the
// model's pick, an implied line, and (MLB) starting pitchers. Research only.
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
      {sections.map((lg) => {
        const games = gamesByLeague[lg.code] ?? [];
        return (
          <section key={lg.code}>
            <h2 className="mb-3 text-sm font-bold uppercase tracking-wide text-ink">{lg.label}</h2>
            {loading ? (
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
                {Array.from({ length: 3 }).map((_, i) => (
                  <div key={i} className="h-28 rounded-2xl border border-white/5 bg-surface animate-pulse-soft" />
                ))}
              </div>
            ) : games.length === 0 ? (
              <div className="rounded-2xl border border-dashed border-white/10 bg-surface/40 py-10 text-center text-sm text-ink-dim">
                No {lg.label} games today.
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
                {games.map((g, i) => (
                  <GameCard key={i} game={g} />
                ))}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}

function GameCard({ game }: { game: Game }) {
  const homeFav = game.home_win_pct >= game.away_win_pct;
  const homePct = Math.round(game.home_win_pct * 100);
  const awayPct = Math.round(game.away_win_pct * 100);

  return (
    <div className="rounded-2xl border border-white/5 bg-surface p-4">
      <div className="text-sm font-bold text-ink">
        {game.away} @ {game.home}
      </div>

      {/* win-prob bar */}
      <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-coral/30">
        <div className="h-full rounded-full bg-violet" style={{ width: `${homePct}%` }} />
      </div>
      <div className="mt-1.5 flex justify-between text-xs">
        <span className={homeFav ? "font-bold text-ink" : "text-ink-dim"}>
          {game.home} {homePct}%
        </span>
        <span className={!homeFav ? "font-bold text-ink" : "text-ink-dim"}>
          {game.away} {awayPct}%
        </span>
      </div>

      <div className="mt-3 text-xs text-ink-dim">
        Model: <span className="font-semibold text-ink">{game.model_pick}</span> wins
        {game.implied_line && <> · Implied line: {game.implied_line}</>}
      </div>
      {game.starters && (
        <div className="mt-1 text-xs text-ink-dim">
          SP: {game.starters.away} vs {game.starters.home}
        </div>
      )}
    </div>
  );
}
