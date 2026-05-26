"""Generate today's MLB strikeout predictions and find edges vs PrizePicks lines.

Pipeline:
  1. Pull today's MLB games + probable pitchers from MLB Stats API
  2. For each pitcher, build inference feature vector
  3. Score with trained model
  4. Convert to full distribution -> P(over X) for each prop line
  5. Compare to current PrizePicks strikeouts_pitcher lines
  6. Output ranked edges
"""
import json
from datetime import date, datetime
from pathlib import Path
import requests
import pandas as pd
import numpy as np
import lightgbm as lgb
from scipy import stats as scipy_stats
from sqlalchemy import text

from props.utils.db import engine, session_scope
from props.utils.logging import log, configure_logging
from props.features.inference import build_full_feature_vector


MODEL_PATH = Path("models/strikeouts_v1.txt")
META_PATH = Path("models/strikeouts_v1_meta.json")


def load_model():
    log.info("loading_model", path=str(MODEL_PATH))
    model = lgb.Booster(model_file=str(MODEL_PATH))
    with open(META_PATH) as f:
        meta = json.load(f)
    return model, meta


def fetch_todays_schedule_with_pitchers(target_date: date) -> list[dict]:
    """MLB Stats API exposes probable pitchers in the schedule."""
    url = "https://statsapi.mlb.com/api/v1/schedule"
    params = {
        "sportId": 1,
        "date": target_date.strftime("%Y-%m-%d"),
        "hydrate": "probablePitcher",
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()

    games = []
    for block in data.get("dates", []):
        for g in block.get("games", []):
            home_pp = g["teams"]["home"].get("probablePitcher")
            away_pp = g["teams"]["away"].get("probablePitcher")
            games.append({
                "external_id": str(g["gamePk"]),
                "game_datetime": g["gameDate"],
                "home_pitcher_external_id": str(home_pp["id"]) if home_pp else None,
                "home_pitcher_name": home_pp["fullName"] if home_pp else None,
                "away_pitcher_external_id": str(away_pp["id"]) if away_pp else None,
                "away_pitcher_name": away_pp["fullName"] if away_pp else None,
                "home_team_external_id": str(g["teams"]["home"]["team"]["id"]),
                "away_team_external_id": str(g["teams"]["away"]["team"]["id"]),
                "status": g["status"]["abstractGameState"],
            })
    return games


def resolve_external_to_internal_ids(games: list[dict]) -> list[dict]:
    """Translate MLB Stats API external IDs to our internal player_id / team_id."""
    pitcher_ext_ids = set()
    team_ext_ids = set()
    for g in games:
        if g["home_pitcher_external_id"]:
            pitcher_ext_ids.add(g["home_pitcher_external_id"])
        if g["away_pitcher_external_id"]:
            pitcher_ext_ids.add(g["away_pitcher_external_id"])
        team_ext_ids.add(g["home_team_external_id"])
        team_ext_ids.add(g["away_team_external_id"])

    with session_scope() as session:
        pitcher_rows = session.execute(text("""
            SELECT external_id, player_id FROM players
            WHERE sport_code='mlb' AND external_id = ANY(:ids)
        """), {"ids": list(pitcher_ext_ids)}).all()
        team_rows = session.execute(text("""
            SELECT external_id, team_id FROM teams
            WHERE sport_code='mlb' AND external_id = ANY(:ids)
        """), {"ids": list(team_ext_ids)}).all()

    pid_map = {row[0]: row[1] for row in pitcher_rows}
    tid_map = {row[0]: row[1] for row in team_rows}

    resolved = []
    for g in games:
        resolved.append({
            **g,
            "home_pitcher_id": pid_map.get(g["home_pitcher_external_id"]),
            "away_pitcher_id": pid_map.get(g["away_pitcher_external_id"]),
            "home_team_id": tid_map.get(g["home_team_external_id"]),
            "away_team_id": tid_map.get(g["away_team_external_id"]),
        })
    return resolved


def build_pitcher_features_for_today(games: list[dict], target_date: date,
                                     season: str, feature_keys: list[str]):
    """For each scheduled pitcher, build inference features.

    Pitchers here are predicting their OWN K count, so the "opposing team"
    in build_full_feature_vector is irrelevant -- we use the offensive team
    they're facing for opposing_lineup features instead. We compute those
    separately because inference.py is currently batter-oriented.
    """
    rows = []
    for g in games:
        for side in ["home", "away"]:
            pid = g[f"{side}_pitcher_id"]
            if pid is None:
                continue
            opposing_side = "away" if side == "home" else "home"
            opp_team_id = g[f"{opposing_side}_team_id"]
            if opp_team_id is None:
                continue

            # Build pitcher's own rolling features using the batter_features helper
            # (it iterates over ALL_STATS, which includes pitching stats too).
            from props.features.inference import batter_features
            feats = batter_features(pid, target_date, season)

            # Add opposing-lineup features (the missing piece for pitchers).
            opp_feats = _opposing_lineup_features(opp_team_id, target_date)
            feats.update(opp_feats)

            rows.append({
                "pitcher_id": pid,
                "pitcher_name": g[f"{side}_pitcher_name"],
                "game_external_id": g["external_id"],
                "opponent_team_id": opp_team_id,
                "side": side,
                **{k: feats.get(k, 0) for k in feature_keys},
            })
    return pd.DataFrame(rows)


def _opposing_lineup_features(team_id: int, before_date: date) -> dict:
    """Compute the opposing team's rolling K rate and offense over their prior games."""
    sql = """
        SELECT g.game_date,
               SUM((pg.stats->>'strikeouts')::int) AS team_k,
               SUM((pg.stats->>'plate_appearances')::int) AS team_pa,
               SUM((pg.stats->>'hits')::int) AS team_hits,
               SUM((pg.stats->>'total_bases')::int) AS team_tb,
               SUM((pg.stats->>'runs')::int) AS team_runs,
               SUM((pg.stats->>'walks')::int) AS team_walks
        FROM player_games pg
        JOIN games g USING (game_id)
        WHERE pg.team_id = :tid
          AND g.sport_code='mlb'
          AND g.game_date < :d
          AND (pg.stats->>'plate_appearances')::int > 0
        GROUP BY g.game_id, g.game_date
        ORDER BY g.game_date
    """
    df = pd.read_sql(text(sql), engine, params={"tid": team_id, "d": before_date})
    if df.empty:
        return {f"lineup_last_{w}_{k}": 0 for w in [10, 20]
                for k in ["k_rate", "avg_runs", "avg_tb", "walk_rate"]}

    feats = {}
    for w in [10, 20]:
        win = df.iloc[-w:]
        sum_k = win["team_k"].sum()
        sum_pa = win["team_pa"].sum()
        n = len(win)
        feats[f"lineup_last_{w}_k_rate"] = (
            round(sum_k / sum_pa, 4) if sum_pa > 0 else 0
        )
        feats[f"lineup_last_{w}_avg_runs"] = round(win["team_runs"].sum() / n, 4)
        feats[f"lineup_last_{w}_avg_tb"] = round(win["team_tb"].sum() / n, 4)
        feats[f"lineup_last_{w}_walk_rate"] = (
            round(win["team_walks"].sum() / sum_pa, 4) if sum_pa > 0 else 0
        )
    return feats


def predict_and_score(model, feature_df: pd.DataFrame,
                     feature_keys: list[str]) -> pd.DataFrame:
    X = feature_df[feature_keys].astype(float)
    pred_lambda = model.predict(X, num_iteration=model.best_iteration)
    out = feature_df[["pitcher_id", "pitcher_name", "side"]].copy()
    out["predicted_mean_k"] = np.round(pred_lambda, 3)
    out["lambda"] = pred_lambda
    return out


def attach_lines_and_edges(predictions: pd.DataFrame) -> pd.DataFrame:
    """Pull tonight's strikeouts_pitcher lines and compute edge per line."""
    pitcher_ids = predictions["pitcher_id"].tolist()
    sql = """
        SELECT DISTINCT ON (player_id, line_value, line_variant)
            player_id, line_value, line_variant, snapshot_at
        FROM prop_lines
        WHERE sportsbook='prizepicks'
          AND sport_code='mlb'
          AND stat_type='strikeouts_pitcher'
          AND player_id = ANY(:ids)
        ORDER BY player_id, line_value, line_variant, snapshot_at DESC
    """
    lines = pd.read_sql(text(sql), engine, params={"ids": pitcher_ids})

    if lines.empty:
        log.info("no_strikeouts_lines_in_db")
        return pd.DataFrame()

    merged = lines.merge(predictions, left_on="player_id", right_on="pitcher_id")
    # P(actual > line) under Poisson with predicted lambda
    merged["p_over"] = 1 - scipy_stats.poisson.cdf(
        merged["line_value"].astype(int), merged["lambda"]
    )
    merged["p_under"] = 1 - merged["p_over"]
    # Edge = how far model's prob is from 50% (PrizePicks pickem implied prob)
    merged["edge_over"] = merged["p_over"] - 0.5
    merged["edge_under"] = merged["p_under"] - 0.5
    merged["recommended"] = np.where(
        merged["edge_over"].abs() > merged["edge_under"].abs(),
        np.where(merged["edge_over"] > 0, "OVER", "UNDER"),
        np.where(merged["edge_under"] > 0, "UNDER", "OVER"),
    )
    merged["edge_abs"] = np.maximum(merged["edge_over"].abs(),
                                    merged["edge_under"].abs())

    cols = ["pitcher_name", "line_variant", "line_value", "predicted_mean_k",
            "p_over", "p_under", "recommended", "edge_abs"]
    return merged[cols].sort_values("edge_abs", ascending=False)


def main():
    configure_logging()
    today = date.today()
    season = str(today.year)
    log.info("predicting_for_date", date=today.isoformat())

    model, meta = load_model()
    feature_keys = meta["feature_keys"]

    games = fetch_todays_schedule_with_pitchers(today)
    log.info("scheduled_games", n=len(games))
    games = resolve_external_to_internal_ids(games)

    feature_df = build_pitcher_features_for_today(games, today, season, feature_keys)
    log.info("built_features", pitchers=len(feature_df))
    if feature_df.empty:
        log.warning("no_pitchers_to_predict")
        return

    predictions = predict_and_score(model, feature_df, feature_keys)
    print("\n=== Today's pitcher K predictions ===")
    print(predictions.sort_values("predicted_mean_k", ascending=False).to_string(index=False))

    edges = attach_lines_and_edges(predictions)
    if edges.empty:
        print("\n(No PrizePicks strikeouts lines matched current pitchers.)")
    else:
        print("\n=== Edges vs PrizePicks ===")
        print(edges.head(30).to_string(index=False))


if __name__ == "__main__":
    main()
