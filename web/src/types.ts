// Mirrors the /api contracts exactly. The frontend renders purely from these —
// it never computes edge, recommendation, confidence, or labels.

export type Lean = "over" | "under"; // model lean, NOT a bet
export type Direction = "over" | "under" | "both";
export type SortKey = "edge" | "confidence" | "start";

// last-N form: true = hit, false = miss, null = push (exact line)
export type FormResult = boolean | null;

export interface Weather {
  temp_f: number | null;
  note: string; // e.g. "wind out +21", "dome (neutral)"
}

export interface Player {
  name: string;
  team: string;
  headshot_url: string | null;
  team_logo_url: string | null;
  watched: boolean;
}

export interface Pick {
  id: string;
  league: string;
  player: Player;
  matchup: string;
  stat_type: string; // display label, e.g. "Strikeouts Batter"
  stat_key?: string; // internal key, e.g. "strikeouts_batter"
  start_time?: string | null;
  line: number;
  model_projection: number;
  likely_range: string; // e.g. "0–1"
  edge_pct: number;
  recommendation: Lean;
  recommended: boolean; // clears the per-category cutoff
  model_confidence: number; // calibrated %, 0–100
  kelly_pct: number; // paper / hypothetical sizing, %
  weather: Weather | null;
  form: FormResult[]; // recent-first
  l5: string; // "x/5"
  l10: string; // "x/10"
  insight: string;
}

export interface SlateLeg {
  player: string;
  league: string;
  stat_type: string;
  line: number;
  confidence: number;
  recommendation: Lean;
  stake_pct?: number; // paper slate-Kelly stake, % of bankroll
}

export interface TopSlate {
  n: number;
  payout: number;
  games: number;
  joint_hit_pct: number;
  max_stake_pct?: number; // paper sizing cap per leg
  legs: SlateLeg[];
}

export interface Summary {
  today: number;
  recommended: number;
  avg_edge_pct: number;
  w: number;
  l: number;
  win_rate_pct: number;
}

export interface StatOption {
  key: string;
  label: string;
  count: number;
}

export interface League {
  code: string;
  label: string;
  count: number;
  available: boolean; // false = calendar-gated / "soon"
  stats: StatOption[];
}

export interface PicksResponse {
  summary: Summary;
  top_slate: TopSlate | null;
  picks: Pick[];
}

export interface Game {
  home: string;
  away: string;
  home_win_pct: number;
  away_win_pct: number;
  model_pick: string;
  implied_line: string | null;
  starters?: { home: string; away: string } | null;
}

// ── Performance ──────────────────────────────────────────────────────────────
export interface PerfRecord {
  pct: number;
  w: number;
  l: number;
  over_breakeven?: number;
}
export interface Performance {
  recommended: PerfRecord;
  all_picks: PerfRecord;
  clv_pct: number;
  trend: { i: number; pct: number }[];
  by_sport: { sport: string; w: number; l: number; pct: number }[];
  roi_by_sport: { sport: string; roi: number }[];
  calibration: { pred: number; actual: number; n: number }[];
  brier: number | null;
  by_market: { market: string; lean: Lean; pct: number; n: number }[];
}

// ── Soft Lines ───────────────────────────────────────────────────────────────
export interface SoftLine {
  player: { name: string; team: string; headshot_url: string | null };
  league: string;
  stat_type: string;
  pp_line: number;
  sharp_line: number | null;
  recommendation: Lean;
  market_ev_pct: number;
  consensus_prob: number;
  sharp_over_prob: number;
}
