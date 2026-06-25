// API client. Uses same-origin relative URLs (Vite proxies /api -> :8000 in dev).
// Set VITE_USE_MOCK=1 to render the contract-shaped fixtures with no backend.
import type { League, Pick } from "./types";
import { MOCK_LEAGUES, MOCK_PICKS } from "./mockData";

const USE_MOCK = import.meta.env.VITE_USE_MOCK === "1";

function mockDelay<T>(value: T): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), 350));
}

export async function fetchLeagues(): Promise<League[]> {
  if (USE_MOCK) return mockDelay(MOCK_LEAGUES);
  const res = await fetch("/api/leagues");
  if (!res.ok) throw new Error(`leagues ${res.status}`);
  const data = (await res.json()) as { leagues: League[] };
  return data.leagues;
}

export async function fetchPicks(league?: string, stat?: string): Promise<Pick[]> {
  if (USE_MOCK) {
    let picks = MOCK_PICKS;
    if (league) picks = picks.filter((p) => p.league === league);
    if (stat) picks = picks.filter((p) => p.stat_key === stat);
    return mockDelay(picks);
  }
  const params = new URLSearchParams();
  if (league) params.set("league", league);
  if (stat) params.set("stat", stat);
  const qs = params.toString();
  const res = await fetch(`/api/picks${qs ? `?${qs}` : ""}`);
  if (!res.ok) throw new Error(`picks ${res.status}`);
  const data = (await res.json()) as { picks: Pick[] };
  return data.picks;
}
