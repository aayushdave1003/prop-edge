import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchGames, fetchLeagues, fetchPicks } from "./api";
import type { Direction, Game, League, PicksResponse, SortKey } from "./types";
import { DisclaimerBanner } from "./components/DisclaimerBanner";
import { TopNav, type Tab } from "./components/TopNav";
import { Sidebar } from "./components/Sidebar";
import { SummaryRow } from "./components/SummaryRow";
import { SportSelector } from "./components/SportSelector";
import { StatFilter } from "./components/StatFilter";
import { SortControl } from "./components/SortControl";
import { TopSlate } from "./components/TopSlate";
import { PickBoard } from "./components/PickBoard";
import { GamePredictions } from "./components/GamePredictions";
import { Footer } from "./components/Footer";
import { SearchIcon } from "./components/icons";
import { EmptyState, ErrorState, SkeletonBoard } from "./components/states";

type Status = "loading" | "ok" | "error";

export default function App() {
  const [tab, setTab] = useState<Tab>("Today's Picks");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [theme, setTheme] = useState<"dark" | "light">(() =>
    new URLSearchParams(window.location.search).get("theme") === "light" ? "light" : "dark",
  );

  const [leagues, setLeagues] = useState<League[]>([]);
  const [league, setLeague] = useState<string | null>(null);
  const [stats, setStats] = useState<string[]>([]);
  const [direction, setDirection] = useState<Direction>("both");
  const [recommendedOnly, setRecommendedOnly] = useState(false);
  const [sort, setSort] = useState<SortKey>("edge");

  // client-side utilities (no storage — session only)
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [watched, setWatched] = useState<Set<string>>(new Set());
  const [watchedOnly, setWatchedOnly] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const [data, setData] = useState<PicksResponse | null>(null);
  const [status, setStatus] = useState<Status>("loading");
  const [asOf, setAsOf] = useState("—");

  const [games, setGames] = useState<Record<string, Game[]>>({});
  const [gamesStatus, setGamesStatus] = useState<Status>("loading");

  function flash(msg: string) {
    setToast(msg);
    setTimeout(() => setToast((t) => (t === msg ? null : t)), 2200);
  }

  const loadLeagues = useCallback(async () => {
    const lg = await fetchLeagues();
    setLeagues(lg);
    setLeague((cur) => cur ?? lg.find((l) => l.available)?.code ?? lg[0]?.code ?? null);
  }, []);

  const loadPicks = useCallback(async () => {
    setStatus("loading");
    try {
      const resp = await fetchPicks({ league: league ?? undefined, stats, direction, recommendedOnly });
      setData(resp);
      setStatus("ok");
      setAsOf(new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }));
    } catch {
      setStatus("error");
    }
  }, [league, stats, direction, recommendedOnly]);

  const loadGames = useCallback(async () => {
    setGamesStatus("loading");
    try {
      const avail = leagues.filter((l) => l.available);
      const results = await Promise.all(avail.map((l) => fetchGames(l.code).then((g) => [l.code, g] as const)));
      setGames(Object.fromEntries(results));
      setGamesStatus("ok");
    } catch {
      setGamesStatus("error");
    }
  }, [leagues]);

  useEffect(() => {
    loadLeagues().catch(() => setStatus("error"));
  }, [loadLeagues]);
  useEffect(() => {
    if (tab === "Today's Picks") void loadPicks();
  }, [tab, loadPicks]);
  useEffect(() => {
    if (tab === "Game Predictions" && leagues.length) void loadGames();
  }, [tab, leagues, loadGames]);

  const activeLeague = useMemo(() => leagues.find((l) => l.code === league) ?? null, [leagues, league]);

  // client-side player search + watchlist filtering (server already filtered the rest)
  const visiblePicks = useMemo(() => {
    let p = data?.picks ?? [];
    const q = query.trim().toLowerCase();
    if (q) p = p.filter((x) => x.player.name.toLowerCase().includes(q));
    if (watchedOnly) p = p.filter((x) => watched.has(x.id));
    return p;
  }, [data, query, watchedOnly, watched]);

  function onSelectLeague(code: string) {
    setLeague(code);
    setStats([]);
  }
  function toggleStat(key: string) {
    setStats((cur) => (cur.includes(key) ? cur.filter((k) => k !== key) : [...cur, key]));
  }
  function toggleWatch(id: string) {
    setWatched((cur) => {
      const next = new Set(cur);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }
  function refresh() {
    if (tab === "Game Predictions") void loadGames();
    else void loadPicks();
  }

  const sidebarActions = {
    theme,
    watchedOnly,
    watchCount: watched.size,
    onToggleTheme: () => setTheme((t) => (t === "dark" ? "light" : "dark")),
    onPlayerLookup: () => {
      setTab("Today's Picks");
      setSearchOpen(true);
    },
    onToggleWatchlist: () => {
      setTab("Today's Picks");
      setWatchedOnly((v) => !v);
    },
    onShare: async () => {
      try {
        await navigator.clipboard.writeText(`${window.location.origin}/?view=results`);
        flash("Results link copied to clipboard");
      } catch {
        flash("Copy blocked by browser");
      }
    },
    onOps: () => flash("Ops · cost & usage lives in the research dashboard"),
    onPickHistory: () => setTab("Recent Picks"),
  };

  return (
    <div className={theme === "light" ? "light" : undefined}>
      <div className="app-bg min-h-screen">
        <DisclaimerBanner />
        <TopNav
          tab={tab}
          onTab={setTab}
          asOf={asOf}
          onRefresh={refresh}
          onToggleSidebar={() => setSidebarOpen((o) => !o)}
        />

        <div className="flex">
          <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} actions={sidebarActions} />

          <main className="mx-auto w-full max-w-[1300px] flex-1 px-4 pb-16 pt-5 sm:px-6">
            {tab === "Today's Picks" && (
              <div className="space-y-5">
                <SummaryRow summary={data?.summary ?? null} />
                <SportSelector leagues={leagues} active={league} onSelect={onSelectLeague} />
                <StatFilter
                  stats={activeLeague?.stats ?? []}
                  selected={stats}
                  onToggleStat={toggleStat}
                  onClear={() => setStats([])}
                  direction={direction}
                  onDirection={setDirection}
                  recommendedOnly={recommendedOnly}
                  onRecommendedOnly={setRecommendedOnly}
                />

                {/* player search (toggled from the sidebar's Player lookup) */}
                {searchOpen && (
                  <div className="flex items-center gap-2 rounded-full bg-surface px-3 py-2 ring-1 ring-violet/40">
                    <SearchIcon className="h-4 w-4 text-ink-dim" />
                    <input
                      autoFocus
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      placeholder="Search players…"
                      className="flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-ink-dim"
                    />
                    <button
                      onClick={() => {
                        setQuery("");
                        setSearchOpen(false);
                      }}
                      className="text-xs font-semibold text-ink-dim hover:text-ink"
                    >
                      Clear
                    </button>
                  </div>
                )}

                {status === "ok" && data?.top_slate && <TopSlate slate={data.top_slate} />}

                <div className="flex items-center justify-between gap-3">
                  <h1 className="text-sm font-semibold text-ink-dim">
                    {status === "ok" && data ? (
                      <>
                        <span className="text-ink">{visiblePicks.length}</span> model lean
                        {visiblePicks.length === 1 ? "" : "s"}
                        {activeLeague ? ` · ${activeLeague.label}` : ""}
                        {watchedOnly && " · watchlist"}
                      </>
                    ) : (
                      "Loading board…"
                    )}
                  </h1>
                  <SortControl value={sort} onChange={setSort} />
                </div>

                {status === "loading" && <SkeletonBoard />}
                {status === "error" && <ErrorState onRetry={refresh} />}
                {status === "ok" && visiblePicks.length === 0 && <EmptyState />}
                {status === "ok" && visiblePicks.length > 0 && (
                  <PickBoard picks={visiblePicks} sort={sort} watched={watched} onToggleWatch={toggleWatch} />
                )}
              </div>
            )}

            {tab === "Game Predictions" && (
              <div className="space-y-5">
                <SportSelector leagues={leagues} active={league} onSelect={onSelectLeague} />
                {gamesStatus === "error" ? (
                  <ErrorState onRetry={loadGames} />
                ) : (
                  <GamePredictions leagues={leagues} gamesByLeague={games} loading={gamesStatus === "loading"} />
                )}
              </div>
            )}

            {(tab === "Performance" || tab === "Soft Lines" || tab === "Recent Picks") && (
              <Placeholder tab={tab} />
            )}

            <Footer />
          </main>
        </div>

        {/* toast */}
        {toast && (
          <div className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-full bg-surface px-4 py-2 text-sm font-semibold text-ink shadow-violet-soft ring-1 ring-violet/40">
            {toast}
          </div>
        )}
      </div>
    </div>
  );
}

function Placeholder({ tab }: { tab: Tab }) {
  const copy: Record<string, string> = {
    Performance: "Settled-pick track record — win rate, ROI, and calibration over time (paper / hypothetical).",
    "Soft Lines": "Props where the model's projection diverges most from the posted line.",
    "Recent Picks": "The rolling history of model leans and how they settled.",
  };
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-white/10 bg-surface/40 py-24 text-center">
      <div className="mb-3 text-4xl">📊</div>
      <p className="text-lg font-semibold text-ink">{tab}</p>
      <p className="mt-1 max-w-sm text-sm text-ink-dim">{copy[tab]}</p>
      <p className="mt-3 text-[11px] uppercase tracking-wide text-amber/80">
        Research / paper-tracking — wired to the same model output
      </p>
    </div>
  );
}
