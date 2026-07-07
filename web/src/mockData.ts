// Contract-shaped fixtures for offline UI work (VITE_USE_MOCK=1). Shapes match
// /api exactly. All numbers are illustrative — hypothetical, not betting advice.
import type { Game, League, PicksResponse } from "./types";

export const MOCK_LEAGUES: League[] = [
  { code: "mlb", label: "MLB", count: 171, available: true, stats: [
    { key: "hits_runs_rbis", label: "H+R+RBI", count: 40 },
    { key: "strikeouts_batter", label: "Strikeouts Batter", count: 22 },
    { key: "strikeouts_pitcher", label: "Pitcher Ks", count: 18 },
    { key: "total_bases", label: "Total Bases", count: 14 },
    { key: "rbis", label: "RBIs", count: 9 },
  ]},
  { code: "wnba", label: "WNBA", count: 112, available: true, stats: [
    { key: "points", label: "Points", count: 22 },
    { key: "pts_rebs", label: "Pts+Rebs", count: 22 },
    { key: "pts_rebs_asts", label: "PRA", count: 20 },
    { key: "pts_asts", label: "Pts+Asts", count: 15 },
    { key: "rebounds", label: "Rebounds", count: 14 },
    { key: "assists", label: "Assists", count: 6 },
  ]},
  { code: "nba", label: "NBA", count: 0, available: false, stats: [] },
  { code: "nhl", label: "NHL", count: 0, available: false, stats: [] },
  { code: "nfl", label: "NFL", count: 0, available: false, stats: [] },
  { code: "cfb", label: "CFB", count: 0, available: false, stats: [] },
  { code: "cbb", label: "CBB", count: 0, available: false, stats: [] },
  { code: "soccer", label: "Soccer", count: 0, available: false, stats: [] },
];

export const MOCK_PICKS: PicksResponse = {
  summary: { today: 171, recommended: 8, avg_edge_pct: 13.5, w: 402, l: 4, win_rate_pct: 49 },
  top_slate: {
    n: 4, payout: 10, games: 4, joint_hit_pct: 9, max_stake_pct: 10,
    legs: [
      { player: "Nick Kurtz", league: "mlb", stat_type: "Strikeouts Batter", line: 1.5, confidence: 56, recommendation: "under", stake_pct: 4.1 },
      { player: "Riley Greene", league: "mlb", stat_type: "Total Bases", line: 1.5, confidence: 58, recommendation: "over", stake_pct: 5.0 },
      { player: "Hunter Brown", league: "mlb", stat_type: "Pitcher Ks", line: 6.5, confidence: 61, recommendation: "over", stake_pct: 6.2 },
      { player: "A'ja Wilson", league: "wnba", stat_type: "PRA", line: 41.5, confidence: 57, recommendation: "over", stake_pct: 4.6 },
    ],
  },
  picks: [
    {
      id: "p1", league: "mlb",
      player: { name: "Nick Kurtz", team: "ATH", headshot_url: null, team_logo_url: null, watched: false },
      matchup: "@ SF", stat_type: "Strikeouts Batter", stat_key: "strikeouts_batter",
      start_time: "2026-06-25T19:45:00Z",
      line: 1.5, model_projection: 1.0, likely_range: "0–1",
      edge_pct: 12.0, recommendation: "under", recommended: true, model_confidence: 56,
      kelly_pct: 17.3,
      weather: { temp_f: 60, note: "wind out +21" },
      form: [false, false, false, false, false], l5: "5/5", l10: "10/10",
      insight: "hit UNDER 5/5 last 5 · +25% vs market",
    },
    {
      id: "p2", league: "mlb",
      player: { name: "Riley Greene", team: "DET", headshot_url: null, team_logo_url: null, watched: true },
      matchup: "vs CLE", stat_type: "Total Bases", stat_key: "total_bases",
      start_time: "2026-06-25T17:10:00Z",
      line: 1.5, model_projection: 2.1, likely_range: "1–3",
      edge_pct: 18.4, recommendation: "over", recommended: true, model_confidence: 62,
      kelly_pct: 11.2,
      weather: { temp_f: 74, note: "calm" },
      form: [true, true, false, true, true], l5: "4/5", l10: "7/10",
      insight: "hit OVER 4/5 last 5 · +18% vs market",
    },
    {
      id: "p3", league: "wnba",
      player: { name: "A'ja Wilson", team: "LV", headshot_url: null, team_logo_url: null, watched: false },
      matchup: "vs SEA", stat_type: "PRA", stat_key: "pts_rebs_asts",
      start_time: "2026-06-25T23:00:00Z",
      line: 41.5, model_projection: 45.8, likely_range: "40–51",
      edge_pct: 10.4, recommendation: "over", recommended: true, model_confidence: 57,
      kelly_pct: 6.8,
      weather: null,
      form: [true, false, true, true, true], l5: "4/5", l10: "8/10",
      insight: "hit OVER 4/5 last 5 · line moving toward the model",
    },
    {
      id: "p4", league: "wnba",
      player: { name: "Caitlin Clark", team: "IND", headshot_url: null, team_logo_url: null, watched: false },
      matchup: "@ CON", stat_type: "Assists", stat_key: "assists",
      start_time: "2026-06-25T23:30:00Z",
      line: 8.5, model_projection: 7.2, likely_range: "5–9",
      edge_pct: -9.1, recommendation: "under", recommended: false, model_confidence: 53,
      kelly_pct: 0,
      weather: null,
      form: [false, true, false, false, true], l5: "3/5", l10: "5/10",
      insight: "model 53% confident",
    },
  ],
};

export const MOCK_GAMES: Game[] = [
  {
    home: "Giants", away: "Athletics", home_win_pct: 0.58, away_win_pct: 0.42,
    model_pick: "Giants", implied_line: "Giants -1.2",
    starters: { home: "Logan Webb", away: "Luis Severino" },
  },
  {
    home: "Tigers", away: "Guardians", home_win_pct: 0.54, away_win_pct: 0.46,
    model_pick: "Tigers", implied_line: "Tigers -0.5",
    starters: { home: "Tarik Skubal", away: "Tanner Bibee" },
  },
];
