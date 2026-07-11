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
  lines_paused?: boolean; // scrape source blocked → no new slates
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
// Every rate is the AUDITED number: forward-only (no lookahead) + point-in-time
// (each cutoff sees only prior settlements). `verdict` is honest vs breakeven —
// "edge" only when the Wilson CI floor actually clears it.
export type Verdict = "edge" | "not proven" | "below breakeven" | "—";
export interface PerfRecord {
  pct: number;
  w: number;
  l: number;
  n?: number;
  lo?: number; // Wilson 95% CI lower bound, %
  hi?: number; // Wilson 95% CI upper bound, %
  verdict?: Verdict;
}
export interface Performance {
  recommended: PerfRecord;
  all_picks: PerfRecord;
  clv_pct: number;
  breakeven: number; // per-leg parlay breakeven, % (57.7)
  method: string; // how the track record is measured
  trend: { i: number; pct: number }[];
  by_sport: { sport: string; w: number; l: number; pct: number; lo: number; hi: number; verdict: Verdict }[];
  roi_by_sport: { sport: string; roi: number }[];
  calibration: { pred: number; actual: number; n: number }[];
  brier: number | null;
  by_market: { market: string; lean: Lean; pct: number; n: number; lo: number; hi: number }[];
  sleeper: SleeperRoi;
}

// Live track record on Sleeper (an ODDS book): realized ROI of the +EV tier
// (a pick is +EV iff CALIBRATED prob × payout > 1). "up = good" here means money
// made. "building" = fewer than MIN_TIER_N settled +EV picks, so no verdict yet.
export type SleeperVerdict = "profitable" | "not proven" | "losing" | "building" | "—";
export interface SleeperRoi {
  n_all: number; // total settled Sleeper picks
  n: number; // +EV tier size
  roi: number; // realized ROI, %
  lo: number; // 95% CI lower, %
  hi: number; // 95% CI upper, %
  hit: number; // hit rate, %
  avg_payout: number; // avg multiplier of the +EV tier
  verdict: SleeperVerdict;
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
