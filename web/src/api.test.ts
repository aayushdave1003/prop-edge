import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchGames, fetchLeagues, fetchPerformance, fetchPicks, fetchSoftLines } from "./api";

// These tests assert the client's URL/query-string building and response
// unwrapping — the contract between the board and /api. fetch is mocked so they
// stay deterministic and never hit the network. (VITE_USE_MOCK is unset here, so
// the real fetch branch runs.)

type Json = unknown;
function mockFetch(payload: Json) {
  const spy = vi.fn(async () => ({ ok: true, status: 200, json: async () => payload }) as Response);
  vi.stubGlobal("fetch", spy);
  return spy;
}

// last call's args (avoid Array.prototype.at — build tsc targets ES2020)
function lastCall(spy: ReturnType<typeof vi.fn>): unknown[] {
  const calls = spy.mock.calls;
  return calls[calls.length - 1] ?? [];
}
function calledUrl(spy: ReturnType<typeof vi.fn>): string {
  return String(lastCall(spy)[0]);
}
function calledInit(spy: ReturnType<typeof vi.fn>): RequestInit | undefined {
  return lastCall(spy)[1] as RequestInit | undefined;
}

afterEach(() => vi.unstubAllGlobals());

describe("fetchLeagues", () => {
  it("GETs /api/leagues and unwraps { leagues }", async () => {
    const spy = mockFetch({ leagues: [{ code: "mlb" }] });
    const out = await fetchLeagues();
    expect(calledUrl(spy)).toBe("/api/leagues");
    expect(out).toEqual([{ code: "mlb" }]);
  });
});

describe("fetchPicks", () => {
  it("builds no query string when the query is empty", async () => {
    const spy = mockFetch({ picks: [] });
    await fetchPicks({});
    expect(calledUrl(spy)).toBe("/api/picks");
  });

  it("encodes league, repeats stat, direction, and recommended", async () => {
    const spy = mockFetch({ picks: [] });
    await fetchPicks({
      league: "mlb",
      stats: ["points", "rebounds"],
      direction: "over",
      recommendedOnly: true,
    });
    const url = calledUrl(spy);
    expect(url.startsWith("/api/picks?")).toBe(true);
    const qs = new URLSearchParams(url.split("?")[1]);
    expect(qs.get("league")).toBe("mlb");
    expect(qs.getAll("stat")).toEqual(["points", "rebounds"]);
    expect(qs.get("direction")).toBe("over");
    expect(qs.get("recommended")).toBe("1");
  });

  it("omits direction when it is 'both'", async () => {
    const spy = mockFetch({ picks: [] });
    await fetchPicks({ direction: "both" });
    expect(calledUrl(spy)).toBe("/api/picks");
  });

  it("throws on a non-ok response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 500, json: async () => ({}) }) as Response),
    );
    await expect(fetchPicks({})).rejects.toThrow("picks 500");
  });
});

describe("signal threading", () => {
  it("passes the AbortSignal through to fetch", async () => {
    const spy = mockFetch({ picks: [] });
    const ac = new AbortController();
    await fetchPicks({}, ac.signal);
    expect(calledInit(spy)?.signal).toBe(ac.signal);
  });

  it("threads the signal on games/perf/soft too", async () => {
    const ac = new AbortController();
    let spy = mockFetch([]);
    await fetchGames("mlb", ac.signal);
    expect(calledUrl(spy)).toBe("/api/games?league=mlb");
    expect(calledInit(spy)?.signal).toBe(ac.signal);

    spy = mockFetch({ ok: true });
    await fetchPerformance(ac.signal);
    expect(calledInit(spy)?.signal).toBe(ac.signal);

    spy = mockFetch({ soft_lines: [] });
    await fetchSoftLines(undefined, ac.signal);
    expect(calledUrl(spy)).toBe("/api/soft_lines");
    expect(calledInit(spy)?.signal).toBe(ac.signal);
  });
});

describe("fetchGames", () => {
  it("accepts a bare array response", async () => {
    mockFetch([{ home: "A", away: "B" }]);
    const out = await fetchGames("mlb");
    expect(out).toEqual([{ home: "A", away: "B" }]);
  });

  it("unwraps a { games } response", async () => {
    mockFetch({ games: [{ home: "A", away: "B" }] });
    const out = await fetchGames();
    expect(out).toEqual([{ home: "A", away: "B" }]);
  });
});
