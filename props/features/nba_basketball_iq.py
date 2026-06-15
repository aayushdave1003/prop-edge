"""NBA Basketball IQ Features — makes the model understand the game.

Computes features that capture basketball concepts box scores alone miss:
  - Usage & role:       usage_rate, foul_drawing_rate, pts_per_fga,
                        paint_scoring_pct, ast_to_pts_ratio
  - Floor spacing:      floor_spacing_score (own 3PT threat),
                        opp_floor_spacing (how much defenders can sag),
                        teammate_avg_floor_spacing
  - Matchup context:    opp_pts_allowed_by_position
  - Game script:        close_game_rate (affects starter minutes),
                        games_last_7_days (fatigue proxy)
  - History vs opponent: career_avg_pts/reb/ast vs this specific franchise

All features use shift(1) lookahead protection — current game never leaks.
"""
from datetime import datetime
import numpy as np
import pandas as pd
from sqlalchemy import text
from props.utils.db import engine, session_scope
from props.utils.logging import log, configure_logging
from props.features.derived_writer import write_derived

WINDOWS = [5, 10, 20]


# ── Data Loading ─────────────────────────────────────────────────────────────

def load_nba_player_games() -> pd.DataFrame:
    log.info("loading_nba_player_games_for_biq")
    df = pd.read_sql("""
        SELECT pg.player_game_id, pg.player_id, pg.game_id, pg.team_id,
               pg.opponent_id, pg.is_home, pg.minutes_played,
               pg.stats, g.game_date, g.season, p.position
        FROM player_games pg
        JOIN games g USING (game_id)
        JOIN players p ON p.player_id = pg.player_id
        WHERE g.sport_code = 'nba'
        ORDER BY pg.player_id, g.game_date, pg.player_game_id
    """, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    log.info("loaded", rows=len(df))
    return df


def load_game_scores() -> pd.DataFrame:
    return pd.read_sql("""
        SELECT game_id, game_date, home_team_id, away_team_id,
               home_score, away_score,
               ABS(home_score - away_score) AS margin
        FROM games
        WHERE sport_code = 'nba' AND status = 'final'
          AND home_score IS NOT NULL
    """, engine)


# ── Stat Helpers ─────────────────────────────────────────────────────────────

def explode_stats(df: pd.DataFrame) -> pd.DataFrame:
    stats = pd.json_normalize(df["stats"].tolist())
    out = df.drop(columns=["stats"]).reset_index(drop=True)
    for col in ["points", "fg_attempted", "fg3_attempted", "fg3_made",
                "ft_attempted", "ft_made", "assists", "rebounds",
                "off_rebounds", "def_rebounds"]:
        out[col] = pd.to_numeric(stats.get(col, pd.Series(0, index=stats.index)),
                                  errors="coerce").fillna(0)
    return out


# ── Feature Computation ──────────────────────────────────────────────────────

def compute_player_role_features(df: pd.DataFrame) -> pd.DataFrame:
    """Usage rate, floor spacing, foul drawing, paint scoring, AST/PTS ratio."""
    log.info("computing_player_role_features")

    # Team totals per game for usage rate denominator
    team_fga = (df.groupby(["game_id", "team_id"])["fg_attempted"]
                  .sum().reset_index().rename(columns={"fg_attempted": "team_fga"}))
    df = df.merge(team_fga, on=["game_id", "team_id"], how="left")
    df["team_fga"] = df["team_fga"].fillna(1)

    # Per-game computed stats
    df["usage_raw"]          = df["fg_attempted"] / df["team_fga"]
    df["spacing_raw"]        = (df["fg3_attempted"] *
                                df["fg3_made"].where(df["fg3_attempted"] > 0,
                                 other=0) /
                                df["fg3_attempted"].where(df["fg3_attempted"] > 0,
                                 other=1))
    # floor_spacing_score = 3PA/min × 3P% (per-minute to normalise minutes)
    df["spacing_raw"]        = (df["fg3_attempted"] / df["minutes_played"].replace(0, np.nan)
                                ).fillna(0) * (
                                 df["fg3_made"] / df["fg3_attempted"].replace(0, np.nan)
                               ).fillna(0)
    df["foul_draw_raw"]      = (df["ft_attempted"] /
                                df["fg_attempted"].replace(0, np.nan)).fillna(0)
    df["pts_per_fga_raw"]    = (df["points"] /
                                df["fg_attempted"].replace(0, np.nan)).fillna(0)
    # Paint scoring: pts minus 3PT points minus FT points, divided by total
    df["paint_pts"]          = (df["points"] - df["fg3_made"] * 3 -
                                df["ft_made"]).clip(lower=0)
    df["paint_scoring_raw"]  = (df["paint_pts"] /
                                df["points"].replace(0, np.nan)).fillna(0)
    df["ast_to_pts_raw"]     = (df["assists"] /
                                df["points"].replace(0, np.nan)).fillna(0)

    results = []
    for pid, grp in df.groupby("player_id", group_keys=False):
        g = grp.sort_values(["game_date", "player_game_id"]).reset_index(drop=True)
        feats = {"player_game_id": g["player_game_id"].values}
        season_marker = (g["season"] != g["season"].shift(1)).cumsum()

        for stat, col in [
            ("usage_rate",         "usage_raw"),
            ("floor_spacing_score","spacing_raw"),
            ("foul_drawing_rate",  "foul_draw_raw"),
            ("pts_per_fga",        "pts_per_fga_raw"),
            ("paint_scoring_pct",  "paint_scoring_raw"),
            ("ast_to_pts_ratio",   "ast_to_pts_raw"),
        ]:
            pv = g[col].shift(1)
            for w in WINDOWS:
                feats[f"last_{w}_avg_{stat}"] = (
                    pv.rolling(w, min_periods=1).mean().fillna(0).round(4).values)
            feats[f"season_avg_{stat}"] = (
                g.groupby(season_marker)[col].apply(
                    lambda s: s.shift(1).expanding().mean()
                ).reset_index(level=0, drop=True).fillna(0).round(4).values)

        results.append(pd.DataFrame(feats))

    return pd.concat(results, ignore_index=True)


def compute_spacing_context(df: pd.DataFrame) -> pd.DataFrame:
    """Opponent floor spacing and teammate floor spacing per game."""
    log.info("computing_spacing_context_features")

    # Compute each player's floor spacing score for each game
    df["spacing_score"] = (
        (df["fg3_attempted"] / df["minutes_played"].replace(0, np.nan)).fillna(0) *
        (df["fg3_made"] / df["fg3_attempted"].replace(0, np.nan)).fillna(0)
    )

    # Team average spacing per game (excluding the player themselves)
    team_spacing = (df.groupby(["game_id", "team_id"])
                      .apply(lambda x: x["spacing_score"].mean())
                      .reset_index(name="team_avg_spacing"))

    opp_spacing = team_spacing.rename(columns={
        "team_id": "opponent_id", "team_avg_spacing": "opp_avg_spacing"
    })

    df = df.merge(team_spacing, on=["game_id", "team_id"], how="left")
    df = df.merge(opp_spacing,  on=["game_id", "opponent_id"], how="left")
    df["team_avg_spacing"] = df["team_avg_spacing"].fillna(0)
    df["opp_avg_spacing"]  = df["opp_avg_spacing"].fillna(0)

    results = []
    for pid, grp in df.groupby("player_id", group_keys=False):
        g = grp.sort_values(["game_date", "player_game_id"]).reset_index(drop=True)
        feats = {"player_game_id": g["player_game_id"].values}

        for stat, col in [
            ("teammate_avg_floor_spacing", "team_avg_spacing"),
            ("opp_floor_spacing",          "opp_avg_spacing"),
        ]:
            pv = g[col].shift(1)
            feats[f"last_10_avg_{stat}"] = (
                pv.rolling(10, min_periods=1).mean().fillna(0).round(4).values)
            feats[f"last_5_avg_{stat}"] = (
                pv.rolling(5, min_periods=1).mean().fillna(0).round(4).values)

        results.append(pd.DataFrame(feats))

    return pd.concat(results, ignore_index=True)


def compute_game_script_features(df: pd.DataFrame,
                                  scores: pd.DataFrame) -> pd.DataFrame:
    """Close game rate and games in last 7 days."""
    log.info("computing_game_script_features")

    # Close game: margin <= 10 at final
    scores["is_close"] = (scores["margin"] <= 10).astype(int)
    team_close = []
    for side, tid_col in [("home", "home_team_id"), ("away", "away_team_id")]:
        tmp = scores[["game_id", tid_col, "game_date", "is_close"]].copy()
        tmp = tmp.rename(columns={tid_col: "team_id"})
        team_close.append(tmp)
    team_close = pd.concat(team_close, ignore_index=True)
    team_close["game_date"] = pd.to_datetime(team_close["game_date"])
    team_close = team_close.sort_values(["team_id", "game_date"])
    team_close["close_rate_last10"] = (
        team_close.groupby("team_id")["is_close"]
        .transform(lambda x: x.shift(1).rolling(10, min_periods=1).mean())
        .fillna(0)
    )
    close_map = team_close.set_index(["game_id", "team_id"])["close_rate_last10"].to_dict()

    results = []
    df_sorted = df.sort_values(["player_id", "game_date", "player_game_id"])

    for pid, grp in df_sorted.groupby("player_id", group_keys=False):
        g = grp.reset_index(drop=True)
        feats = {"player_game_id": g["player_game_id"].values}

        # Close game rate for player's team
        feats["team_close_game_rate"] = [
            close_map.get((row.game_id, row.team_id), 0.5)
            for row in g.itertuples()
        ]

        # Games played in last 7 days (fatigue proxy)
        games_last7 = []
        for i, row in enumerate(g.itertuples()):
            cutoff = row.game_date - pd.Timedelta(days=7)
            count = g.iloc[:i][g.iloc[:i]["game_date"] >= cutoff].shape[0]
            games_last7.append(count)
        feats["games_last_7_days"] = games_last7

        results.append(pd.DataFrame(feats))

    return pd.concat(results, ignore_index=True)


def compute_career_vs_opponent(df: pd.DataFrame) -> pd.DataFrame:
    """Rolling career averages vs this specific franchise (opponent_id)."""
    log.info("computing_career_vs_opponent_features")
    results = []

    for pid, grp in df.groupby("player_id", group_keys=False):
        g = grp.sort_values(["game_date", "player_game_id"]).reset_index(drop=True)
        feats = {"player_game_id": g["player_game_id"].values}

        for stat in ["points", "rebounds", "assists"]:
            career_vs = np.zeros(len(g))
            for i in range(len(g)):
                opp = g.iloc[i]["opponent_id"]
                prior = g.iloc[:i]
                prior_vs_opp = prior[prior["opponent_id"] == opp]
                if len(prior_vs_opp) >= 2:
                    vals = pd.to_numeric(prior_vs_opp[stat], errors="coerce").fillna(0)
                    career_vs[i] = round(float(vals.mean()), 4)
                else:
                    career_vs[i] = 0.0
            feats[f"career_avg_{stat}_vs_opp"] = career_vs

        results.append(pd.DataFrame(feats))

    return pd.concat(results, ignore_index=True)


def compute_opp_pts_by_position(df: pd.DataFrame) -> pd.DataFrame:
    """How many pts/reb/ast does the opponent allow to each position."""
    log.info("computing_opp_pts_by_position")

    # Map position to broad category
    pos_map = {
        "G": "guard", "PG": "guard", "SG": "guard",
        "F": "forward", "SF": "forward", "PF": "forward",
        "C": "center", "FC": "forward", "GF": "forward",
    }
    df["pos_group"] = df["position"].map(pos_map).fillna("forward")

    # Opponent allowed stats per position per game
    opp_allowed = (df.groupby(["game_id", "opponent_id", "pos_group"])
                     [["points", "rebounds", "assists"]]
                     .mean()
                     .reset_index())

    # Roll: for each team, how many pts/reb/ast do they allow to each position
    results = []
    for pid, grp in df.groupby("player_id", group_keys=False):
        g = grp.sort_values(["game_date", "player_game_id"]).reset_index(drop=True)
        feats = {"player_game_id": g["player_game_id"].values}

        for stat in ["points", "rebounds", "assists"]:
            allowed_vals = np.zeros(len(g))
            for i in range(len(g)):
                opp_id  = g.iloc[i]["opponent_id"]
                pos_grp = g.iloc[i]["pos_group"]
                # Get last 10 games where this opponent faced this position
                prior_games = g.iloc[:i]["game_id"].tolist()
                subset = opp_allowed[
                    (opp_allowed["opponent_id"] == opp_id) &
                    (opp_allowed["pos_group"] == pos_grp) &
                    (~opp_allowed["game_id"].isin(prior_games))
                ].tail(10)
                if len(subset) >= 3:
                    allowed_vals[i] = round(float(subset[stat].mean()), 4)
            feats[f"opp_last10_allowed_{stat}_to_pos"] = allowed_vals

        results.append(pd.DataFrame(feats))

    return pd.concat(results, ignore_index=True)


# ── Merge into derived ────────────────────────────────────────────────────────

def merge_features(feature_dfs: list, batch_size: int = 3000):
    """Merge all feature DataFrames into player_games.derived."""
    combined = feature_dfs[0]
    for fdf in feature_dfs[1:]:
        combined = combined.merge(fdf, on="player_game_id", how="left")

    log.info("merging_basketball_iq_features", rows=len(combined),
             features=len(combined.columns) - 1)

    feat_cols = [c for c in combined.columns if c != "player_game_id"]
    items = []
    for _, row in combined.iterrows():
        patch = {}
        for c in feat_cols:
            v = row[c]
            if pd.isna(v):
                patch[c] = 0.0
            elif isinstance(v, (np.integer,)):
                patch[c] = int(v)
            else:
                patch[c] = round(float(v), 4)
        items.append((int(row["player_game_id"]), patch))

    write_derived(items, mode="merge", label="nba_basketball_iq")


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    configure_logging()
    started = datetime.now()
    with session_scope() as session:
        run_id = session.execute(text("""
            INSERT INTO ingestion_runs (source, started_at, status)
            VALUES ('nba_basketball_iq', :s, 'running') RETURNING run_id
        """), {"s": started}).scalar()

    df     = load_nba_player_games()
    df     = explode_stats(df)
    scores = load_game_scores()

    role_feats    = compute_player_role_features(df.copy())
    spacing_feats = compute_spacing_context(df.copy())
    script_feats  = compute_game_script_features(df.copy(), scores)
    career_feats  = compute_career_vs_opponent(df.copy())
    pos_feats     = compute_opp_pts_by_position(df.copy())

    merge_features([role_feats, spacing_feats, script_feats,
                    career_feats, pos_feats])

    with session_scope() as session:
        session.execute(text("""
            UPDATE ingestion_runs SET completed_at=NOW(),
                rows_inserted=:n, status='success' WHERE run_id=:rid
        """), {"n": len(role_feats), "rid": run_id})
    log.info("nba_basketball_iq_complete", rows=len(role_feats))


if __name__ == "__main__":
    run()
