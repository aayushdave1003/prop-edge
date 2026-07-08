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
import { MethodologyModal } from "./components/MethodologyModal";
import { Footer } from "./components/Footer";
import { SearchIcon } from "./components/icons";
import { EmptyState, ErrorState, SkeletonBoard } from "./components/states";

type Status = "loading" | "ok" | "error";
const SORT_LABEL: Record<SortKey, string> = { edge: "edge", confidence: "confidence", start: "start time" };

// A fetch aborted by a superseded request throws AbortError — ignore it so a
// stale in-flight response never overwrites fresh state or flips us to "error".
const isAbort = (e: unknown) => e instanceof DOMException && e.name === "AbortError";

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
  const [helpOpen, setHelpOpen] = useState(false);

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

  const loadLeagues = useCallback(async (signal?: AbortSignal) => {
    try {
      const lg = await fetchLeagues(signal);
      setLeagues(lg);
      // Default to the cross-sport "All" view so the landing shows the best 4-leg
      // slate across every sport.
      setLeague((cur) => cur ?? "all");
    } catch (e) {
      if (!isAbort(e)) setStatus("error");
    }
  }, []);

  const loadPicks = useCallback(
    async (signal?: AbortSignal) => {
      setStatus("loading");
      try {
        const resp = await fetchPicks(
          {
            league: league && league !== "all" ? league : undefined, // "all" = no league filter
            stats,
            direction,
            recommendedOnly,
          },
          signal,
        );
        setData(resp);
        setStatus("ok");
        setAsOf(new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }));
      } catch (e) {
        if (!isAbort(e)) setStatus("error");
      }
    },
    [league, stats, direction, recommendedOnly],
  );

  const loadGames = useCallback(
    async (signal?: AbortSignal) => {
      setGamesStatus("loading");
      try {
        const avail = leagues.filter((l) => l.available);
        const res = await Promise.all(
          avail.map((l) => fetchGames(l.code, signal).then((g) => [l.code, g] as const)),
        );
        setGames(Object.fromEntries(res));
        setGamesStatus("ok");
      } catch (e) {
        if (!isAbort(e)) setGamesStatus("error");
      }
    },
    [leagues],
  );
  const loadPerf = useCallback(async (signal?: AbortSignal) => {
    setPerfStatus("loading");
    try {
      setPerf(await fetchPerformance(signal));
      setPerfStatus("ok");
    } catch (e) {
      if (!isAbort(e)) setPerfStatus("error");
    }
  }, []);
  const loadSoft = useCallback(async (signal?: AbortSignal) => {
    setSoftStatus("loading");
    try {
      setSoft(await fetchSoftLines(undefined, signal)); // market-wide list (not tied to the picks league)
      setSoftStatus("ok");
    } catch (e) {
      if (!isAbort(e)) setSoftStatus("error");
    }
  }, []);

  useEffect(() => {
    const ac = new AbortController();
    void loadLeagues(ac.signal);
    return () => ac.abort();
  }, [loadLeagues]);
  useEffect(() => {
    if (tab !== "Today's Picks") return;
    const ac = new AbortController();
    void loadPicks(ac.signal);
    return () => ac.abort();
  }, [tab, loadPicks]);
  useEffect(() => {
    if (tab !== "Game Predictions" || !leagues.length) return;
    const ac = new AbortController();
    void loadGames(ac.signal);
    return () => ac.abort();
  }, [tab, leagues, loadGames]);
  useEffect(() => {
    if (tab !== "Performance") return;
    const ac = new AbortController();
    void loadPerf(ac.signal);
    return () => ac.abort();
  }, [tab, loadPerf]);
  useEffect(() => {
    if (tab !== "Soft Lines") return;
    const ac = new AbortController();
    void loadSoft(ac.signal);
    return () => ac.abort();
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
      <TopNav tab={tab} onTab={setTab} asOf={asOf} onRefresh={refresh} onHelp={() => setHelpOpen(true)} />
      <MethodologyModal open={helpOpen} onClose={() => setHelpOpen(false)} />

      <main className="mx-auto max-w-[1400px] px-5 pb-[70px] pt-[26px]">
        {tab === "Today's Picks" && (
          <div className="space-y-[22px]">
            {data?.summary?.lines_paused && (
              <div
                className="rounded-[14px] border px-4 py-3 text-[13px] leading-relaxed text-ink-2"
                style={{ borderColor: "rgba(245,181,68,0.3)", background: "rgba(245,181,68,0.07)" }}
              >
                <b className="text-ink">Model lines paused.</b> The line source (PrizePicks) is
                blocked, so there are <b>no new slates</b> right now. Past picks and the honest track
                record on the <b>Performance</b> tab remain available — this is research / paper-tracking,
                never betting advice.
              </div>
            )}
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
              <div className="tap-target flex items-center gap-2 rounded-[10px] border border-hair bg-white/[0.02] px-3 py-2">
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
              <ErrorState onRetry={() => void loadGames()} />
            ) : (
              <GamePredictions leagues={leagues} gamesByLeague={games} loading={gamesStatus === "loading"} />
            )}
          </div>
        )}

        {tab === "Performance" &&
          (perfStatus === "error" ? (
            <ErrorState onRetry={() => void loadPerf()} />
          ) : (
            <PerformanceView perf={perf} loading={perfStatus === "loading"} />
          ))}

        {tab === "Soft Lines" &&
          (softStatus === "error" ? (
            <ErrorState onRetry={() => void loadSoft()} />
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

