// Mirrors the /api/picks and /api/leagues contracts exactly. The frontend renders
// purely from these shapes — it never computes edge, recommendation, or labels.

export type Recommendation = "more" | "less";
export type Confidence = "low" | "med" | "high";

export interface Player {
  name: string;
  team: string;
  headshot_url: string | null;
}

export interface Pick {
  id: string;
  league: string;
  player: Player;
  matchup: string;
  start_time: string | null;
  stat_type: string; // display label, e.g. "Points"
  stat_key?: string; // internal key, e.g. "points"
  pp_line: number;
  model_projection: number;
  edge_pct: number;
  recommendation: Recommendation;
  confidence: Confidence;
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
  stats: StatOption[];
}

export type SortKey = "edge" | "confidence" | "start";
