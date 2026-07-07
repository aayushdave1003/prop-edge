// API client. Same-origin relative URLs (Vite proxies /api -> :8000 in dev).
// VITE_USE_MOCK=1 renders the contract-shaped fixtures with no backend.
import type { Direction, Game, League, PicksResponse } from "./types";
import { MOCK_GAMES, MOCK_LEAGUES, MOCK_PICKS } from "./mockData";

const USE_MOCK = import.meta.env.VITE_USE_MOCK === "1";

function delay<T>(value: T): Promise<T> {
  return new Promise((r) => setTimeout(() => r(value), 350));
}

export async function fetchLeagues(signal?: AbortSignal): Promise<League[]> {
  if (USE_MOCK) return delay(MOCK_LEAGUES);
  const res = await fetch("/api/leagues", { signal });
  if (!res.ok) throw new Error(`leagues ${res.status}`);
  return (await res.json()).leagues as League[];
}

export interface PickQuery {
  league?: string;
  stats?: string[];
  direction?: Direction;
  recommendedOnly?: boolean;
}

export async function fetchPicks(q: PickQuery, signal?: AbortSignal): Promise<PicksResponse> {
  if (USE_MOCK) {
    let picks = MOCK_PICKS.picks;
    if (q.league) picks = picks.filter((p) => p.league === q.league);
    if (q.stats?.length) picks = picks.filter((p) => p.stat_key && q.stats!.includes(p.stat_key));
    if (q.direction && q.direction !== "both")
      picks = picks.filter((p) => p.recommendation === q.direction);
    if (q.recommendedOnly) picks = picks.filter((p) => p.recommended);
    return delay({ ...MOCK_PICKS, picks });
  }
  const params = new URLSearchParams();
  if (q.league) params.set("league", q.league);
  q.stats?.forEach((s) => params.append("stat", s));
  if (q.direction && q.direction !== "both") params.set("direction", q.direction);
  if (q.recommendedOnly) params.set("recommended", "1");
  const qs = params.toString();
  const res = await fetch(`/api/picks${qs ? `?${qs}` : ""}`, { signal });
  if (!res.ok) throw new Error(`picks ${res.status}`);
  return (await res.json()) as PicksResponse;
}

export async function fetchGames(league?: string, signal?: AbortSignal): Promise<Game[]> {
  if (USE_MOCK) return delay(league && league !== "mlb" ? [] : MOCK_GAMES);
  const params = new URLSearchParams();
  if (league) params.set("league", league);
  const qs = params.toString();
  const res = await fetch(`/api/games${qs ? `?${qs}` : ""}`, { signal });
  if (!res.ok) throw new Error(`games ${res.status}`);
  const data = await res.json();
  return (Array.isArray(data) ? data : data.games) as Game[];
}

export async function fetchPerformance(signal?: AbortSignal): Promise<import("./types").Performance> {
  const res = await fetch("/api/performance", { signal });
  if (!res.ok) throw new Error(`performance ${res.status}`);
  return res.json();
}

export async function fetchSoftLines(league?: string, signal?: AbortSignal): Promise<import("./types").SoftLine[]> {
  const qs = league ? `?league=${league}` : "";
  const res = await fetch(`/api/soft_lines${qs}`, { signal });
  if (!res.ok) throw new Error(`soft_lines ${res.status}`);
  return (await res.json()).soft_lines;
}
