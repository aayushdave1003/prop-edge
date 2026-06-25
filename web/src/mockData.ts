// Contract-shaped fixtures for offline UI work. Enable by running the app with
// VITE_USE_MOCK=1 (see api.ts). Shapes match /api exactly — no extra fields.
import type { League, Pick } from "./types";

export const MOCK_LEAGUES: League[] = [
  {
    code: "nba",
    label: "NBA",
    count: 5,
    stats: [
      { key: "points", label: "Points", count: 2 },
      { key: "rebounds", label: "Rebounds", count: 1 },
      { key: "assists", label: "Assists", count: 1 },
      { key: "pts_rebs_asts", label: "PRA", count: 1 },
    ],
  },
  {
    code: "mlb",
    label: "MLB",
    count: 3,
    stats: [
      { key: "strikeouts_pitcher", label: "Pitcher Ks", count: 2 },
      { key: "total_bases", label: "Total Bases", count: 1 },
    ],
  },
];

export const MOCK_PICKS: Pick[] = [
  {
    id: "m1",
    league: "nba",
    player: { name: "Nikola Jokic", team: "DEN", headshot_url: null },
    matchup: "LAL @ DEN",
    start_time: "2026-06-25T19:30:00Z",
    stat_type: "PRA",
    stat_key: "pts_rebs_asts",
    pp_line: 48.5,
    model_projection: 54.1,
    edge_pct: 11.5,
    recommendation: "more",
    confidence: "high",
  },
  {
    id: "m2",
    league: "nba",
    player: { name: "Anthony Davis", team: "LAL", headshot_url: null },
    matchup: "LAL @ DEN",
    start_time: "2026-06-25T19:30:00Z",
    stat_type: "Points",
    stat_key: "points",
    pp_line: 24.5,
    model_projection: 26.8,
    edge_pct: 9.4,
    recommendation: "more",
    confidence: "high",
  },
  {
    id: "m3",
    league: "nba",
    player: { name: "Austin Reaves", team: "LAL", headshot_url: null },
    matchup: "LAL @ DEN",
    start_time: "2026-06-25T19:30:00Z",
    stat_type: "Assists",
    stat_key: "assists",
    pp_line: 5.5,
    model_projection: 4.7,
    edge_pct: -14.5,
    recommendation: "less",
    confidence: "med",
  },
  {
    id: "m4",
    league: "nba",
    player: { name: "Jamal Murray", team: "DEN", headshot_url: null },
    matchup: "LAL @ DEN",
    start_time: "2026-06-25T19:30:00Z",
    stat_type: "Rebounds",
    stat_key: "rebounds",
    pp_line: 4.5,
    model_projection: 4.6,
    edge_pct: 2.2,
    recommendation: "more",
    confidence: "low",
  },
];
