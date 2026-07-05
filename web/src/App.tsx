import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchGames, fetchLeagues, fetchPerformance, fetchPicks, fetchSoftLines } from "./api";
import type { Direction, Game, League, Performance, PicksResponse, SoftLine, SortKey, StatOption } from "./types";
import { DisclaimerBanner } from "./components/DisclaimerBanner";
import { TABS, TopNav, type Tab } from "./components/TopNav";
import { SummaryRow } from "./components/SummaryRow";
import { SportSelector } from "./components/SportSelector";
import { Controls } from "./components/Controls";
import { TopSlate } from "./components/TopSlate";
import { PickBoard } from "./components/PickBoard";
import { GamePredictions } from "./components/GamePredictions";
import { PerformanceView } from "./components/Performance";
import { SoftLinesView } from "./components/SoftLines";
import { Footer } from "./components/Footer";
import { SearchIcon } from "./components/icons";
import { EmptyState, ErrorState, SkeletonBoard } from "./components/states";

type Status = "loading" | "ok" | "error";
const SORT_LABEL: Record<SortKey, string> = { edge: "edge", confidence: "confidence", start: "start time" };

export default function App() {
  const [tab, setTab] = useState<Tab>(() => {
    const t = new URLSearchParams(window.location.search).get("tab");
    return (TABS as readonly string[]).includes(t ?? "") ? (t as Tab) : "Today's Picks";
  });
  const [leagues, setLeagues] = useState<League[]>([]);
  const [league, setLeague] = useState<string | null>(null);
  const [stats, setStats] = useState<string[]>([]);
  const [direction, setDirection] = useState<Direction>("both");
  const [recommendedOnly, setRecommendedOnly] = useState(false);
  const [sort, setSort] = useState<SortKey>("edge");
  const [watched, setWatched] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [toast, setToast] = useState<string | null>(null);
  const [asOf, setAsOf] = useState("—");

  const [data, setData] = useState<PicksResponse | null>(null);
  const [status, setStatus] = useState<Status>("loading");

  const [games, setGames] = useState<Record<string, Game[]>>({});
  const [gamesStatus, setGamesStatus] = useState<Status>("loading");
  const [perf, setPerf] = useState<Performance | null>(null);
  const [perfStatus, setPerfStatus] = useState<Status>("loading");
  const [soft, setSoft] = useState<SoftLine[]>([]);
  const [softStatus, setSoftStatus] = useState<Status>("loading");

  function flash(msg: string) {
    setToast(msg);
    setTimeout(() => setToast((t) => (t === msg ? null : t)), 2000);
  }

  const loadLeagues = useCallback(async () => {
    const lg = await fetchLeagues();
    setLeagues(lg);
    // Default to the cross-sport "All" view so the landing shows the best 4-leg
    // slate across every sport.
    setLeague((cur) => cur ?? "all");
  }, []);

  const loadPicks = useCallback(async () => {
    setStatus("loading");
    try {
      const resp = await fetchPicks({
        league: league && league !== "all" ? league : undefined, // "all" = no league filter
        stats,
        direction,
        recommendedOnly,
      });
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
      const res = await Promise.all(avail.map((l) => fetchGames(l.code).then((g) => [l.code, g] as const)));
      setGames(Object.fromEntries(res));
      setGamesStatus("ok");
    } catch {
      setGamesStatus("error");
    }
  }, [leagues]);
  const loadPerf = useCallback(async () => {
    setPerfStatus("loading");
    try {
      setPerf(await fetchPerformance());
      setPerfStatus("ok");
    } catch {
      setPerfStatus("error");
    }
  }, []);
  const loadSoft = useCallback(async () => {
    setSoftStatus("loading");
    try {
      setSoft(await fetchSoftLines()); // market-wide list (not tied to the picks league)
      setSoftStatus("ok");
    } catch {
      setSoftStatus("error");
    }
  }, []);

  useEffect(() => {
    loadLeagues().catch(() => setStatus("error"));
  }, [loadLeagues]);
  useEffect(() => {
    if (tab === "Today's Picks") void loadPicks();
  }, [tab, loadPicks]);
  useEffect(() => {
    if (tab === "Game Predictions" && leagues.length) void loadGames();
  }, [tab, leagues, loadGames]);
  useEffect(() => {
    if (tab === "Performance") void loadPerf();
  }, [tab, loadPerf]);
  useEffect(() => {
    if (tab === "Soft Lines") void loadSoft();
  }, [tab, loadSoft]);

  // Prepend a synthetic "All" league (cross-sport): merged market chips + total
  // count. Its picks come from an unfiltered /api/picks; the slate is already global.
  const displayLeagues = useMemo(() => {
    const avail = leagues.filter((l) => l.available);
    const byKey = new Map<string, StatOption>();
    for (const l of avail)
      for (const s of l.stats) {
        const e = byKey.get(s.key);
        if (e) e.count += s.count;
        else byKey.set(s.key, { ...s });
      }
    const all: League = {
      code: "all",
      label: "All",
      count: avail.reduce((n, l) => n + l.count, 0),
      available: true,
      stats: [...byKey.values()].sort((a, b) => b.count - a.count),
    };
    return [all, ...leagues];
  }, [leagues]);

  const activeLeague = useMemo(
    () => displayLeagues.find((l) => l.code === league) ?? null,
    [displayLeagues, league],
  );
  const visiblePicks = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? (data?.picks ?? []).filter((p) => p.player.name.toLowerCase().includes(q)) : (data?.picks ?? []);
  }, [data, query]);

  function toggleStat(key: string) {
    setStats((cur) => (cur.includes(key) ? cur.filter((k) => k !== key) : [...cur, key]));
  }
  function toggleWatch(id: string) {
    setWatched((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }
  function refresh() {
    void loadPicks();
    flash("Board refreshed");
  }

  return (
    <div className="app-grid min-h-screen bg-app-glow">
      <DisclaimerBanner />
      <TopNav tab={tab} onTab={setTab} asOf={asOf} onRefresh={refresh} />

      <main className="mx-auto max-w-[1400px] px-5 pb-[70px] pt-[26px]">
        {tab === "Today's Picks" && (
          <div className="space-y-[22px]">
            <SummaryRow summary={data?.summary ?? null} />
            <SportSelector
              leagues={displayLeagues}
              active={league}
              onSelect={(c) => {
                setLeague(c);
                setStats([]);
              }}
              onUnavailable={(lbl) => flash(`${lbl} — not in season yet`)}
            />
            <Controls
              stats={activeLeague?.stats ?? []}
              selected={stats}
              onToggleStat={toggleStat}
              onClear={() => setStats([])}
              direction={direction}
              onDirection={setDirection}
              recommendedOnly={recommendedOnly}
              onRecommendedOnly={setRecommendedOnly}
              sort={sort}
              onSort={setSort}
            />

            {status === "ok" && data?.top_slate && !recommendedOnly && !query && (
              <TopSlate slate={data.top_slate} />
            )}

            {/* board header */}
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="text-[13px] text-ink-2">
                <b className="tnum text-ink">{visiblePicks.length}</b> model lean
                {visiblePicks.length === 1 ? "" : "s"}
                {activeLeague ? ` · ${activeLeague.label}` : ""} · ranked by {SORT_LABEL[sort]}
              </div>
              <div className="flex items-center gap-2 rounded-[10px] border border-hair bg-white/[0.02] px-3 py-2">
                <SearchIcon className="h-4 w-4 text-ink-3" />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search players"
                  className="w-40 bg-transparent text-[13px] text-ink outline-none placeholder:text-ink-3"
                />
              </div>
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
            <SportSelector
              leagues={displayLeagues}
              active={league}
              onSelect={setLeague}
              onUnavailable={(lbl) => flash(`${lbl} — not in season yet`)}
            />
            {gamesStatus === "error" ? (
              <ErrorState onRetry={loadGames} />
            ) : (
              <GamePredictions leagues={leagues} gamesByLeague={games} loading={gamesStatus === "loading"} />
            )}
          </div>
        )}

        {tab === "Performance" &&
          (perfStatus === "error" ? (
            <ErrorState onRetry={loadPerf} />
          ) : (
            <PerformanceView perf={perf} loading={perfStatus === "loading"} />
          ))}

        {tab === "Soft Lines" &&
          (softStatus === "error" ? (
            <ErrorState onRetry={loadSoft} />
          ) : (
            <SoftLinesView lines={soft} loading={softStatus === "loading"} />
          ))}

        <Footer />
      </main>

      {toast && (
        <div className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-full border border-hair bg-[#12101d] px-4 py-2 text-[13px] font-semibold text-ink shadow-btn">
          {toast}
        </div>
      )}
    </div>
  );
}

