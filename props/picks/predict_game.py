"""Game-level winner prediction: win probability + implied spread.

Loads the trained nba_winner_v1 classifier and regressor, computes team
rolling features for tonight's matchups, and returns predictions that can
be compared against the sportsbook spread/moneyline.
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


CLF_PATH  = Path("models/nba_winner_v1_classifier.txt")
REG_PATH  = Path("models/nba_winner_v1_regressor.txt")
META_PATH = Path("models/nba_winner_v1_meta.json")


def _load_models():
    if not CLF_PATH.exists() or not REG_PATH.exists():
        log.warning("winner_models_not_found", path=str(CLF_PATH))
        return None, None, None
    clf  = lgb.Booster(model_file=str(CLF_PATH))
    reg  = lgb.Booster(model_file=str(REG_PATH))
    with open(META_PATH) as f:
        meta = json.load(f)
    return clf, reg, meta


def get_team_features(team_id: int, before_date: date, is_playoffs: bool = False) -> dict:
    """Query recent team history and compute rolling features for inference."""
    sql = text("""
        WITH team_pts AS (
            SELECT pg.game_id, pg.team_id,
                   SUM((pg.stats->>'points')::float)       AS pts,
                   SUM((pg.stats->>'fg_made')::float)      AS fgm,
                   SUM((pg.stats->>'fg_attempted')::float) AS fga,
                   SUM((pg.stats->>'ft_attempted')::float) AS fta,
                   SUM((pg.stats->>'turnovers')::float)    AS tov,
                   SUM((pg.stats->>'off_rebounds')::float) AS oreb
            FROM player_games pg
            JOIN games g USING (game_id)
            WHERE g.sport_code = 'nba'
              AND g.status = 'final'
              AND g.home_score IS NOT NULL
              AND g.home_score > 0
            GROUP BY pg.game_id, pg.team_id
        )
        SELECT
            g.game_date,
            g.game_id,
            g.season,
            pg.is_home,
            my.pts  AS pts_scored,
            opp.pts AS pts_allowed,
            CASE WHEN my.pts > opp.pts THEN 1 ELSE 0 END AS won,
            COALESCE(my.fga + 0.44*my.fta + my.tov - my.oreb, 0) AS possessions,
            CASE WHEN my.fga > 0 THEN my.fgm / my.fga ELSE 0 END AS fg_pct
        FROM player_games pg
        JOIN games g USING (game_id)
        JOIN team_pts my  ON my.game_id  = pg.game_id AND my.team_id  = pg.team_id
        JOIN team_pts opp ON opp.game_id = pg.game_id AND opp.team_id = pg.opponent_id
        WHERE pg.team_id = :tid
          AND g.sport_code = 'nba'
          AND g.status = 'final'
          AND g.game_date < :d
          AND pg.team_id <> pg.opponent_id
        GROUP BY g.game_date, g.game_id, g.season, pg.is_home,
                 my.pts, opp.pts, my.fgm, my.fga, my.fta, my.tov, my.oreb
        ORDER BY g.game_date DESC
        LIMIT 25
    """)
    df = pd.read_sql(sql, engine, params={"tid": team_id, "d": before_date})
    if df.empty:
        return {}

    df = df.sort_values("game_date").reset_index(drop=True)
    margin = df["pts_scored"] - df["pts_allowed"]

    def _roll_mean(s, w):
        return s.rolling(w, min_periods=1).mean().iloc[-1]

    def _season_mean(s):
        return s.mean() if len(s) > 0 else 0.0

    feats: dict = {}
    for w in [5, 10, 20]:
        feats[f"last_{w}_avg_pts_scored"]  = _roll_mean(df["pts_scored"], w)
        feats[f"last_{w}_avg_pts_allowed"] = _roll_mean(df["pts_allowed"], w)
    for w in [5, 10]:
        feats[f"last_{w}_avg_margin"]  = _roll_mean(margin, w)
        feats[f"last_{w}_win_rate"]    = _roll_mean(df["won"].astype(float), w)

    feats["season_avg_pts_scored"]  = _season_mean(df["pts_scored"])
    feats["season_avg_pts_allowed"] = _season_mean(df["pts_allowed"])
    feats["season_avg_margin"]      = _season_mean(margin)
    feats["season_avg_possessions"] = _season_mean(df["possessions"])
    feats["season_avg_fg_pct"]      = _season_mean(df["fg_pct"])
    feats["season_win_rate"]        = _season_mean(df["won"].astype(float))

    feats["last_10_avg_possessions"] = _roll_mean(df["possessions"], 10)
    feats["last_5_avg_fg_pct"]       = _roll_mean(df["fg_pct"], 5)
    feats["last_10_avg_fg_pct"]      = _roll_mean(df["fg_pct"], 10)

    last_date   = pd.Timestamp(df["game_date"].iloc[-1])
    target_date = pd.Timestamp(before_date)
    days_rest   = max(1, min(14, (target_date - last_date).days))

    feats["days_rest"]           = days_rest
    feats["is_back_to_back"]     = int(days_rest == 1)
    feats["games_played_season"] = len(df)
    feats["is_playoffs"]         = int(is_playoffs)

    return feats


def predict_games(nba_games: list, target_date: date,
                  game_context: dict = None) -> list[dict]:
    """
    For each game in nba_games, compute team features and predict outcome.

    Returns list of prediction dicts, one per game.
    game_context: {game_id: {total, home_spread, implied_home, implied_away, ...}}
    """
    clf, reg, meta = _load_models()
    if clf is None:
        return []

    feature_keys = meta["feature_keys"]
    is_playoffs  = True  # assume playoffs if running during May

    predictions = []
    for g in nba_games:
        gid   = g.get("game_id")
        htid  = g.get("home_team_id")
        atid  = g.get("away_team_id")
        if not gid or not htid or not atid or htid == atid:
            continue

        hf = get_team_features(htid, target_date, is_playoffs)
        af = get_team_features(atid, target_date, is_playoffs)
        if not hf or not af:
            log.warning("missing_team_features", game_id=gid)
            continue

        row = {}
        for feat in meta["team_features"]:
            row[f"home_{feat}"] = float(hf.get(feat, 0))
            row[f"away_{feat}"] = float(af.get(feat, 0))

        X = pd.DataFrame([row])[feature_keys].astype(float)
        home_win_prob = float(clf.predict(X, num_iteration=clf.best_iteration)[0])
        implied_margin = float(reg.predict(X, num_iteration=reg.best_iteration)[0])

        ctx = (game_context or {}).get(gid, {})
        market_spread      = ctx.get("home_spread")     # negative = home favored
        market_total       = ctx.get("total")
        market_home_implied = ctx.get("implied_home")
        market_away_implied = ctx.get("implied_away")

        # Convert market spread to win probability.
        # ESPN home_spread is negative when home team is favored (e.g. -4.5 = home -4.5).
        # Flip sign: home_advantage = -home_spread so positive = home favored.
        # Each point of spread ≈ ~3% win prob (logistic, k=0.15 calibrated to NBA).
        if market_spread is not None:
            home_advantage = -market_spread  # e.g. home_spread=-4.5 → home_advantage=4.5
            market_home_wp = 1 / (1 + np.exp(-home_advantage * 0.15))
        else:
            market_home_wp = None

        predictions.append({
            "game_id":         gid,
            "home_team_id":    htid,
            "away_team_id":    atid,
            "home_win_prob":   round(home_win_prob, 4),
            "away_win_prob":   round(1 - home_win_prob, 4),
            "implied_margin":  round(implied_margin, 1),
            "market_spread":   market_spread,
            "market_total":    market_total,
            "market_home_wp":  round(market_home_wp, 4) if market_home_wp else None,
            "market_edge":     round(home_win_prob - market_home_wp, 4) if market_home_wp else None,
            "market_home_implied": market_home_implied,
            "market_away_implied": market_away_implied,
        })

    return predictions


def _american_odds(win_prob: float) -> str:
    """Convert win probability to American odds string."""
    p = max(0.01, min(0.99, win_prob))
    if p >= 0.5:
        odds = round(-p / (1 - p) * 100)
        return f"{odds}"    # e.g. -150
    else:
        odds = round((1 - p) / p * 100)
        return f"+{odds}"   # e.g. +130


def print_game_predictions(predictions: list[dict], team_names: dict):
    """
    Print game prediction card.
    team_names: {team_id: "City Name"} lookup
    """
    if not predictions:
        return

    print("\n╔══════════════════════════════════════════════════════════╗")
    print("║              GAME WINNER PREDICTIONS                    ║")
    print("╚══════════════════════════════════════════════════════════╝")

    for pred in predictions:
        home = team_names.get(pred["home_team_id"], f"Team {pred['home_team_id']}")
        away = team_names.get(pred["away_team_id"], f"Team {pred['away_team_id']}")
        hwp  = pred["home_win_prob"]
        awp  = pred["away_win_prob"]
        # Use classifier for direction; regressor for magnitude
        margin     = pred["implied_margin"]
        hwp        = pred["home_win_prob"]
        # If classifier and regressor disagree on winner, trust the classifier
        fav        = home if hwp >= 0.5 else away
        fav_margin = abs(margin)

        print(f"\n  {away} @ {home}")
        print(f"  {'─'*54}")

        # Model prediction
        h_odds = _american_odds(hwp)
        a_odds = _american_odds(awp)
        winner = home if hwp > 0.5 else away
        conf   = max(hwp, awp)
        print(f"  Model:   {winner} wins  ({conf:.0%} confidence)")
        print(f"           {home} {h_odds}  |  {away} {a_odds}")
        print(f"           Implied spread: {fav} -{fav_margin:.1f}")

        # Market comparison
        ms = pred.get("market_spread")
        mt = pred.get("market_total")
        me   = pred.get("market_edge")

        if ms is not None:
            # ESPN home_spread: negative = home team is favored (they give points)
            mfav = home if ms <= 0 else away
            print(f"  Market:  {mfav} -{abs(ms):.1f}  |  O/U {mt}")
            if me is not None:
                edge_team  = home if me > 0 else away
                edge_sign  = "+" if me > 0 else ""
                edge_str   = f"Model favors {edge_team} by {abs(me):.0%} vs market"
                print(f"  Edge:    {edge_sign}{me:.0%}  ({edge_str})")

                # Actionable recommendation
                if abs(me) >= 0.05:
                    # me > 0 → model likes home more than market → bet home
                    # me < 0 → model likes away more than market → bet away
                    bet_home = me > 0
                    bet_team = home if bet_home else away
                    # Line from bet team's perspective
                    if bet_home:
                        bet_line = f"-{abs(ms):.1f}" if ms <= 0 else f"+{abs(ms):.1f}"
                    else:
                        bet_line = f"+{abs(ms):.1f}" if ms <= 0 else f"-{abs(ms):.1f}"
                    strength = "STRONG" if abs(me) >= 0.10 else "LEAN"
                    print(f"\n  ► {strength}: {bet_team} {bet_line}  (model edge {edge_sign}{me:.0%})")
                else:
                    print("\n  ► PASS — model and market agree, no meaningful edge")
        else:
            print("  Market:  lines not yet available")
