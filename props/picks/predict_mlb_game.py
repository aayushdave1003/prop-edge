"""MLB game winner prediction: win probability + implied run line.

Loads mlb_winner_v1 classifier and regressor, builds team rolling features
and starting pitcher features for tonight's matchups, returns predictions
comparable to the sportsbook moneyline/run line.
"""
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb
from sqlalchemy import text

from props.utils.db import engine
from props.utils.logging import log


CLF_PATH  = Path("models/mlb_winner_v1_classifier.txt")
REG_PATH  = Path("models/mlb_winner_v1_regressor.txt")
META_PATH = Path("models/mlb_winner_v1_meta.json")


def _load_models():
    if not CLF_PATH.exists() or not REG_PATH.exists():
        log.warning("mlb_winner_models_not_found")
        return None, None, None
    clf = lgb.Booster(model_file=str(CLF_PATH))
    reg = lgb.Booster(model_file=str(REG_PATH))
    with open(META_PATH) as f:
        meta = json.load(f)
    return clf, reg, meta


def _get_team_features(team_id: int, before_date: date) -> dict:
    sql = text("""
        WITH team_runs AS (
            SELECT pg.game_id, pg.team_id,
                   SUM(COALESCE((pg.stats->>'runs')::float, 0))      AS runs,
                   SUM(COALESCE((pg.stats->>'hits')::float, 0))      AS hits,
                   SUM(COALESCE((pg.stats->>'home_runs')::float, 0)) AS hr,
                   SUM(COALESCE((pg.stats->>'walks')::float, 0))     AS bb
            FROM player_games pg
            JOIN games g USING(game_id)
            WHERE g.sport_code = 'mlb' AND g.status = 'final'
            GROUP BY pg.game_id, pg.team_id
        )
        SELECT g.game_date, g.season,
               my.runs  AS runs_scored,
               opp.runs AS runs_allowed,
               my.hits, my.hr, my.bb,
               CASE WHEN my.runs > opp.runs THEN 1 ELSE 0 END AS won
        FROM team_runs my
        JOIN games g ON g.game_id = my.game_id
        JOIN team_runs opp ON opp.game_id = my.game_id
            AND opp.team_id = CASE
                WHEN g.home_team_id = my.team_id THEN g.away_team_id
                ELSE g.home_team_id END
        WHERE g.sport_code = 'mlb'
          AND g.status = 'final'
          AND (g.home_team_id = my.team_id OR g.away_team_id = my.team_id)
          AND my.team_id = :tid
          AND g.game_date < :d
          AND my.runs + opp.runs > 0
        ORDER BY g.game_date DESC
        LIMIT 25
    """)
    df = pd.read_sql(sql, engine, params={"tid": team_id, "d": before_date})
    if df.empty:
        return {}

    df = df.sort_values("game_date").reset_index(drop=True)
    margin = df["runs_scored"] - df["runs_allowed"]

    def _roll(s, w):
        return s.rolling(w, min_periods=1).mean().iloc[-1]

    feats: dict = {}
    for w in [5, 10, 20]:
        feats[f"last_{w}_avg_runs_scored"]  = _roll(df["runs_scored"], w)
        feats[f"last_{w}_avg_runs_allowed"] = _roll(df["runs_allowed"], w)
    for w in [5, 10]:
        feats[f"last_{w}_avg_margin"]  = _roll(margin, w)
        feats[f"last_{w}_win_rate"]    = _roll(df["won"].astype(float), w)

    feats["season_avg_runs_scored"]  = df["runs_scored"].mean()
    feats["season_avg_runs_allowed"] = df["runs_allowed"].mean()
    feats["season_avg_margin"]       = margin.mean()
    feats["season_win_rate"]         = df["won"].astype(float).mean()
    feats["last_10_avg_hits_scored"] = _roll(df["hits"], 10)
    feats["last_10_avg_hr_scored"]   = _roll(df["hr"], 10)
    feats["last_10_avg_bb_scored"]   = _roll(df["bb"], 10)

    last_date   = pd.Timestamp(df["game_date"].iloc[-1])
    target_date = pd.Timestamp(before_date)
    days_rest   = max(1, min(7, (target_date - last_date).days))
    feats["days_rest"]             = days_rest
    feats["is_back_to_back"]       = int(days_rest == 1)
    feats["games_played_season"]   = len(df)
    return feats


def _get_pitcher_features(pitcher_id: int, before_date: date) -> dict:
    """Rolling ERA, WHIP, K/9 for a starting pitcher."""
    sql = text("""
        SELECT g.game_date,
               (pg.stats->>'outs_recorded')::float    AS outs,
               (pg.stats->>'earned_runs')::float      AS er,
               (pg.stats->>'hits_allowed')::float     AS h,
               (pg.stats->>'walks_allowed')::float    AS bb,
               (pg.stats->>'strikeouts_pitcher')::float AS k
        FROM player_games pg
        JOIN games g USING(game_id)
        WHERE pg.player_id = :pid
          AND g.sport_code = 'mlb'
          AND g.status = 'final'
          AND (pg.stats->>'outs_recorded')::float >= 9
          AND g.game_date < :d
        ORDER BY g.game_date DESC
        LIMIT 15
    """)
    df = pd.read_sql(sql, engine, params={"pid": pitcher_id, "d": before_date})
    if df.empty:
        return {"sp_last_3_era": 4.5, "sp_last_5_era": 4.5, "sp_last_10_era": 4.5,
                "sp_last_3_whip": 1.35, "sp_last_5_whip": 1.35, "sp_last_10_whip": 1.35,
                "sp_last_3_k9": 8.0, "sp_last_5_k9": 8.0, "sp_last_10_k9": 8.0,
                "sp_season_era": 4.5, "sp_season_whip": 1.35, "sp_season_k9": 8.0,
                "sp_games_started": 0}

    df = df.sort_values("game_date").reset_index(drop=True)
    df["ip"]   = df["outs"] / 3.0
    df["era"]  = (df["er"] / df["ip"].clip(lower=0.33)) * 9
    df["whip"] = (df["h"] + df["bb"]) / df["ip"].clip(lower=0.33)
    df["k9"]   = (df["k"] / df["ip"].clip(lower=0.33)) * 9

    def _roll(col, w):
        return df[col].rolling(w, min_periods=1).mean().iloc[-1]

    feats = {}
    for w in [3, 5, 10]:
        feats[f"sp_last_{w}_era"]  = round(_roll("era", w), 3)
        feats[f"sp_last_{w}_whip"] = round(_roll("whip", w), 3)
        feats[f"sp_last_{w}_k9"]   = round(_roll("k9", w), 3)
    feats["sp_season_era"]      = round(df["era"].mean(), 3)
    feats["sp_season_whip"]     = round(df["whip"].mean(), 3)
    feats["sp_season_k9"]       = round(df["k9"].mean(), 3)
    feats["sp_games_started"]   = len(df)
    return feats


def predict_mlb_games(mlb_games: list, target_date: date) -> list[dict]:
    """For each MLB game, predict home win probability and implied run line."""
    clf, reg, meta = _load_models()
    if clf is None:
        return []

    feature_keys   = meta["feature_keys"]
    team_features  = meta["team_features"]
    pitcher_features = meta.get("pitcher_features", [])

    predictions = []
    for g in mlb_games:
        gid  = g.get("game_id")
        htid = g.get("home_team_id")
        atid = g.get("away_team_id")
        if not gid or not htid or not atid or htid == atid:
            continue

        hf = _get_team_features(htid, target_date)
        af = _get_team_features(atid, target_date)
        if not hf or not af:
            log.warning("mlb_missing_team_features", game_id=gid)
            continue

        # Pitcher features
        h_pit_id = g.get("home_pitcher_id")
        a_pit_id = g.get("away_pitcher_id")
        hpf = _get_pitcher_features(h_pit_id, target_date) if h_pit_id else {}
        apf = _get_pitcher_features(a_pit_id, target_date) if a_pit_id else {}

        row = {}
        for feat in team_features:
            row[f"home_{feat}"] = float(hf.get(feat, 0))
            row[f"away_{feat}"] = float(af.get(feat, 0))
        for feat in pitcher_features:
            row[f"home_{feat}"] = float(hpf.get(feat, 4.5 if "era" in feat else (1.35 if "whip" in feat else (8.0 if "k9" in feat else 0))))
            row[f"away_{feat}"] = float(apf.get(feat, 4.5 if "era" in feat else (1.35 if "whip" in feat else (8.0 if "k9" in feat else 0))))

        X = pd.DataFrame([row])[feature_keys].astype(float)
        home_win_prob  = float(clf.predict(X, num_iteration=clf.best_iteration)[0])
        implied_margin = float(reg.predict(X, num_iteration=reg.best_iteration)[0])

        predictions.append({
            "game_id":        gid,
            "home_team_id":   htid,
            "away_team_id":   atid,
            "home_pitcher":   g.get("home_pitcher_name", "TBD"),
            "away_pitcher":   g.get("away_pitcher_name", "TBD"),
            "home_win_prob":  round(home_win_prob, 4),
            "away_win_prob":  round(1 - home_win_prob, 4),
            "implied_margin": round(implied_margin, 2),
        })

    return predictions


def _american_odds(p: float) -> str:
    p = max(0.01, min(0.99, p))
    if p >= 0.5:
        return str(round(-p / (1 - p) * 100))
    return f"+{round((1-p)/p*100)}"


def print_mlb_game_predictions(predictions: list[dict], team_names: dict):
    if not predictions:
        return

    print("\n╔══════════════════════════════════════════════════════════╗")
    print("║            MLB GAME WINNER PREDICTIONS                  ║")
    print("╚══════════════════════════════════════════════════════════╝")

    for pred in predictions:
        home = team_names.get(pred["home_team_id"], f"Team {pred['home_team_id']}")
        away = team_names.get(pred["away_team_id"], f"Team {pred['away_team_id']}")
        hwp  = pred["home_win_prob"]
        awp  = pred["away_win_prob"]
        fav  = home if hwp >= 0.5 else away
        mag  = abs(pred["implied_margin"])

        print(f"\n  {away} @ {home}")
        print(f"  {'─'*54}")
        print(f"  SP:    {pred['away_pitcher']} vs {pred['home_pitcher']}")

        winner = home if hwp > 0.5 else away
        conf   = max(hwp, awp)
        h_odds = _american_odds(hwp)
        a_odds = _american_odds(awp)
        print(f"  Model: {winner} wins  ({conf:.0%} confidence)")
        print(f"         {home} {h_odds}  |  {away} {a_odds}")
        print(f"         Implied run line: {fav} -{mag:.1f}")
