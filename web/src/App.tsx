import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchLeagues, fetchPicks } from "./api";
import type { League, Pick, SortKey } from "./types";
import { TopNav } from "./components/TopNav";
import { LeagueSelector } from "./components/LeagueSelector";
import { StatFilter } from "./components/StatFilter";
import { SortControl } from "./components/SortControl";
import { PickBoard } from "./components/PickBoard";
import { ChatIcon } from "./components/icons";
import { EmptyState, ErrorState, SkeletonBoard } from "./components/states";

type Status = "loading" | "ok" | "error";

export default function App() {
  const [leagues, setLeagues] = useState<League[]>([]);
  const [league, setLeague] = useState<string | null>(null);
  const [stat, setStat] = useState<string | null>(null);
  const [sort, setSort] = useState<SortKey>("edge");

  const [picks, setPicks] = useState<Pick[]>([]);
  const [status, setStatus] = useState<Status>("loading");

  // bootstrap: load available leagues, select the busiest one
  const loadLeagues = useCallback(async () => {
    const lg = await fetchLeagues();
    setLeagues(lg);
    setLeague((cur) => cur ?? lg[0]?.code ?? null);
  }, []);

  // load picks for the current league + stat filter
  const loadPicks = useCallback(async () => {
    setStatus("loading");
    try {
      const p = await fetchPicks(league ?? undefined, stat ?? undefined);
      setPicks(p);
      setStatus("ok");
    } catch {
      setStatus("error");
    }
  }, [league, stat]);

  useEffect(() => {
    loadLeagues().catch(() => setStatus("error"));
  }, [loadLeagues]);

  useEffect(() => {
    void loadPicks();
  }, [loadPicks]);

  const activeLeague = useMemo(
    () => leagues.find((l) => l.code === league) ?? null,
    [leagues, league],
  );
  const stats = activeLeague?.stats ?? [];

  function onSelectLeague(code: string) {
    setLeague(code);
    setStat(null); // reset stat filter when switching leagues
  }

  function retry() {
    loadLeagues().then(loadPicks).catch(() => setStatus("error"));
  }

  return (
    <div className="min-h-screen bg-app-gradient">
      <TopNav />

      <main className="mx-auto max-w-[1400px] px-4 pb-24 pt-5 sm:px-6">
        {/* league selector */}
        <LeagueSelector leagues={leagues} active={league} onSelect={onSelectLeague} />

        {/* stat filter */}
        <div className="mt-3">
          <StatFilter stats={stats} active={stat} onSelect={setStat} />
        </div>

        {/* board header */}
        <div className="mt-6 mb-3 flex items-center justify-between gap-3">
          <h1 className="text-sm font-semibold text-ink-dim">
            {status === "ok" ? (
              <>
                <span className="text-ink">{picks.length}</span> prop
                {picks.length === 1 ? "" : "s"}
                {activeLeague ? ` · ${activeLeague.label}` : ""}
              </>
            ) : (
              "Loading board…"
            )}
          </h1>
          <SortControl value={sort} onChange={setSort} />
        </div>

        {/* board / states */}
        {status === "loading" && <SkeletonBoard />}
        {status === "error" && <ErrorState onRetry={retry} />}
        {status === "ok" && picks.length === 0 && <EmptyState />}
        {status === "ok" && picks.length > 0 && <PickBoard picks={picks} sort={sort} />}

        {/* footer */}
        <footer className="mt-14 flex items-center justify-center gap-3 text-[11px] font-bold uppercase tracking-wide text-amber/90">
          <a href="#" className="transition hover:text-amber">
            Help Center
          </a>
          <span className="h-3 w-px bg-white/15" />
          <a href="#" className="transition hover:text-amber">
            How To Play
          </a>
          <span className="h-3 w-px bg-white/15" />
          <a href="#" className="transition hover:text-amber">
            Scoring Chart
          </a>
        </footer>
      </main>

      {/* floating action button */}
      <button
        className="fixed bottom-6 right-6 z-30 flex h-14 w-14 items-center justify-center rounded-full bg-edge text-black shadow-edge-glow transition hover:brightness-110"
        aria-label="Quick add"
      >
        <ChatIcon className="h-6 w-6" />
      </button>
    </div>
  );
}
