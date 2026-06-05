"""NHL Advanced Stats — makes the model understand hockey.

Skater features:
  shooting_pct     — goals / shots (hot/cold streaks, finishing ability)
  pts_per_toi      — points per minute (efficiency, ice time quality)
  goals_per_shot   — pure shooting efficiency
  pp_toi_share     — powerplay involvement proxy (PP pts / team PP pts)
  hit_rate         — hits per minute (physical style proxy)
  corsi_proxy      — team shots_for / (shots_for + shots_against) — possession
  blocked_per_toi  — blocked shots per minute (defensive role proxy)

Goalie features:
  save_pct_rolling — goals against / shots against
  gaa_rolling      — goals against average
  saves_per_start  — workload measure
"""
import json
from datetime import datetime
import numpy as np
import pandas as pd
from sqlalchemy import text
from props.utils.db import engine, session_scope
from props.utils.logging import log, configure_logging
from props.features.derived_writer import write_derived

WINDOWS = [5, 10, 20]


def load_nhl_player_games() -> pd.DataFrame:
    log.info("loading_nhl_player_games")
    df = pd.read_sql("""
        SELECT pg.player_game_id, pg.player_id, pg.game_id, pg.team_id,
               pg.opponent_id, pg.minutes_played, pg.stats,
               g.game_date, g.season, p.position
        FROM player_games pg
        JOIN games g USING (game_id)
        JOIN players p ON p.player_id = pg.player_id
        WHERE g.sport_code = 'nhl'
        ORDER BY pg.player_id, g.game_date, pg.player_game_id
    """, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    log.info("loaded", rows=len(df))
    return df


def explode_stats(df: pd.DataFrame) -> pd.DataFrame:
    stats = pd.json_normalize(df["stats"].tolist())
    out = df.drop(columns=["stats"]).reset_index(drop=True)
    for col in ["goals", "assists", "points", "shots", "hits", "blocked_shots",
                "powerplay_goals", "powerplay_points", "saves", "goals_against",
                "shots_against", "penalty_minutes", "plus_minus"]:
        out[col] = pd.to_numeric(
            stats.get(col, pd.Series(0, index=stats.index)),
            errors="coerce").fillna(0)
    out["minutes"] = pd.to_numeric(
        stats.get("minutes", pd.Series(0, index=stats.index)),
        errors="coerce").fillna(0)
    return out


def compute_skater_advanced(df: pd.DataFrame) -> pd.DataFrame:
    """Advanced metrics for skaters."""
    log.info("computing_skater_advanced_stats")

    skaters = df[df["position"] != "G"].copy()
    skaters["toi"] = skaters["minutes"].replace(0, np.nan)

    # Per-game computed stats
    skaters["shooting_pct_raw"]   = skaters["goals"] / skaters["shots"].replace(0, np.nan)
    skaters["pts_per_toi_raw"]    = skaters["points"] / skaters["toi"]
    skaters["goals_per_shot_raw"] = skaters["goals"] / skaters["shots"].replace(0, np.nan)
    skaters["hit_rate_raw"]       = skaters["hits"] / skaters["toi"]
    skaters["block_rate_raw"]     = skaters["blocked_shots"] / skaters["toi"]
    skaters["penalty_rate_raw"]   = skaters["penalty_minutes"] / skaters["toi"]

    for col in ["shooting_pct_raw", "pts_per_toi_raw", "goals_per_shot_raw",
                "hit_rate_raw", "block_rate_raw", "penalty_rate_raw"]:
        skaters[col] = skaters[col].fillna(0).clip(0, 2)

    # Team total shots per game for Corsi proxy
    team_shots = (skaters.groupby(["game_id", "team_id"])["shots"]
                         .sum().reset_index().rename(columns={"shots": "team_shots_for"}))
    opp_shots = team_shots.rename(columns={
        "team_id": "opponent_id", "team_shots_for": "opp_shots_for"
    })
    skaters = skaters.merge(team_shots, on=["game_id", "team_id"], how="left")
    skaters = skaters.merge(opp_shots,  on=["game_id", "opponent_id"], how="left")
    skaters["corsi_raw"] = (
        skaters["team_shots_for"]
        / (skaters["team_shots_for"] + skaters["opp_shots_for"]).replace(0, np.nan)
    ).fillna(0.5)

    # PP involvement proxy: this player's PP pts vs team's total PP pts
    team_pp = (skaters.groupby(["game_id", "team_id"])["powerplay_points"]
                      .sum().reset_index().rename(columns={"powerplay_points": "team_pp_pts"}))
    skaters = skaters.merge(team_pp, on=["game_id", "team_id"], how="left")
    skaters["pp_share_raw"] = (
        skaters["powerplay_points"]
        / skaters["team_pp_pts"].replace(0, np.nan)
    ).fillna(0)

    results = []
    for pid, grp in skaters.groupby("player_id", group_keys=False):
        g = grp.sort_values(["game_date", "player_game_id"]).reset_index(drop=True)
        feats = {"player_game_id": g["player_game_id"].values}
        season_marker = (g["season"] != g["season"].shift(1)).cumsum()

        for stat, col in [
            ("shooting_pct",     "shooting_pct_raw"),
            ("pts_per_toi",      "pts_per_toi_raw"),
            ("goals_per_shot",   "goals_per_shot_raw"),
            ("hit_rate",         "hit_rate_raw"),
            ("block_rate",       "block_rate_raw"),
            ("corsi_team",       "corsi_raw"),
            ("pp_toi_share",     "pp_share_raw"),
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

    return pd.concat(results, ignore_index=True) if results else pd.DataFrame()


def compute_goalie_advanced(df: pd.DataFrame) -> pd.DataFrame:
    """Advanced metrics for goalies."""
    log.info("computing_goalie_advanced_stats")

    goalies = df[df["position"] == "G"].copy()
    goalies["save_pct_raw"] = goalies["saves"] / goalies["shots_against"].replace(0, np.nan)
    goalies["gaa_raw"]      = goalies["goals_against"] * (60 / goalies["minutes"].replace(0, np.nan))
    goalies["workload_raw"] = goalies["shots_against"]  # saves per start proxy

    for col in ["save_pct_raw", "gaa_raw", "workload_raw"]:
        goalies[col] = goalies[col].fillna(0)
    goalies["save_pct_raw"] = goalies["save_pct_raw"].clip(0, 1)
    goalies["gaa_raw"]      = goalies["gaa_raw"].clip(0, 10)

    results = []
    for pid, grp in goalies.groupby("player_id", group_keys=False):
        g = grp.sort_values(["game_date", "player_game_id"]).reset_index(drop=True)
        feats = {"player_game_id": g["player_game_id"].values}
        season_marker = (g["season"] != g["season"].shift(1)).cumsum()

        for stat, col in [
            ("goalie_save_pct",  "save_pct_raw"),
            ("goalie_gaa",       "gaa_raw"),
            ("goalie_workload",  "workload_raw"),
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

    return pd.concat(results, ignore_index=True) if results else pd.DataFrame()


def merge_features(feature_dfs: list, batch_size: int = 1000):
    valid = [f for f in feature_dfs if f is not None and not f.empty]
    if not valid:
        return
    combined = valid[0]
    for fdf in valid[1:]:
        combined = pd.concat([combined, fdf], ignore_index=True)

    log.info("merging_nhl_advanced_features", rows=len(combined))
    feat_cols = [c for c in combined.columns if c != "player_game_id"]

    items = []
    for _, row in combined.iterrows():
        patch = {c: round(float(row[c]), 4) for c in feat_cols if not pd.isna(row[c])}
        if patch:
            items.append((int(row["player_game_id"]), patch))
    write_derived(items, mode="merge", label="nhl_advanced_stats")


def run():
    configure_logging()
    started = datetime.now()
    with session_scope() as session:
        run_id = session.execute(text("""
            INSERT INTO ingestion_runs (source, started_at, status)
            VALUES ('nhl_advanced_stats', :s, 'running') RETURNING run_id
        """), {"s": started}).scalar()

    df = load_nhl_player_games()
    if df.empty:
        log.info("no_nhl_data_yet")
        return

    df = explode_stats(df)
    skater_feats = compute_skater_advanced(df.copy())
    goalie_feats = compute_goalie_advanced(df.copy())
    merge_features([skater_feats, goalie_feats])

    with session_scope() as session:
        session.execute(text("""
            UPDATE ingestion_runs SET completed_at=NOW(),
                rows_inserted=:n, status='success' WHERE run_id=:rid
        """), {"n": len(df), "rid": run_id})
    log.info("nhl_advanced_stats_complete", rows=len(df))


if __name__ == "__main__":
    run()
