"""Build feature vectors for upcoming games.

At training time, features live in player_games.derived. At inference time
(predicting tonight's slate), we construct identical features from each
player's most recent history.
"""
import pandas as pd
from sqlalchemy import text
from props.utils.db import engine


WINDOWS = [5, 10, 20]
BATTING_STATS = [
    "hits", "total_bases", "rbis", "runs", "home_runs",
    "strikeouts", "walks", "at_bats", "bat_order_spot",
]
PITCHING_STATS = [
    "strikeouts_pitcher", "outs_recorded", "earned_runs",
    "walks_allowed", "hits_allowed", "batters_faced",
]
ALL_STATS = BATTING_STATS + PITCHING_STATS

THRESHOLDS = {
    "hits": [0.5, 1.5, 2.5],
    "total_bases": [1.5, 2.5, 3.5],
    "rbis": [0.5, 1.5],
    "home_runs": [0.5],
    "strikeouts_pitcher": [4.5, 5.5, 6.5, 7.5],
}


def _player_history(player_id, before_date, season):
    sql = """
        SELECT pg.player_game_id, g.game_date, g.season, pg.stats
        FROM player_games pg
        JOIN games g USING (game_id)
        WHERE pg.player_id = :pid
          AND g.game_date < :d
          AND g.sport_code = 'mlb'
        ORDER BY g.game_date, pg.player_game_id
    """
    df = pd.read_sql(text(sql), engine, params={"pid": player_id, "d": before_date})
    if df.empty:
        return df
    stats_df = pd.json_normalize(df["stats"])
    for col in ALL_STATS:
        if col in stats_df.columns:
            stats_df[col] = pd.to_numeric(stats_df[col], errors="coerce").fillna(0)
        else:
            stats_df[col] = 0
    out = pd.concat([df.drop(columns=["stats"]).reset_index(drop=True),
                     stats_df[ALL_STATS].reset_index(drop=True)], axis=1)
    out["game_date"] = pd.to_datetime(out["game_date"])
    return out


def batter_features(player_id, game_date, season):
    hist = _player_history(player_id, game_date, season)
    features = {}
    if hist.empty:
        for stat in ALL_STATS:
            for w in WINDOWS:
                features[f"last_{w}_avg_{stat}"] = 0
            features[f"season_avg_{stat}"] = 0
            if stat in THRESHOLDS:
                for thr in THRESHOLDS[stat]:
                    features[f"last_10_rate_over_{thr}_{stat}"] = 0
        features["days_rest"] = -1
        features["games_played_season"] = 0
        return features
    last_game_date = hist["game_date"].iloc[-1].date()
    features["days_rest"] = (game_date - last_game_date).days
    features["games_played_season"] = int((hist["season"] == season).sum())
    for stat in ALL_STATS:
        for w in WINDOWS:
            window = hist[stat].iloc[-w:]
            features[f"last_{w}_avg_{stat}"] = (
                round(float(window.mean()), 4) if len(window) > 0 else 0
            )
        season_hist = hist[hist["season"] == season][stat]
        features[f"season_avg_{stat}"] = (
            round(float(season_hist.mean()), 4) if len(season_hist) > 0 else 0
        )
        if stat in THRESHOLDS:
            recent = hist[stat].iloc[-10:]
            for thr in THRESHOLDS[stat]:
                rate = (recent > thr).mean() if len(recent) > 0 else 0
                features[f"last_10_rate_over_{thr}_{stat}"] = round(float(rate), 4)
    return features


def pitcher_quality_features(pitcher_player_id, game_date):
    sql = """
        SELECT g.game_date,
               (pg.stats->>'batters_faced')::int AS bf,
               (pg.stats->>'outs_recorded')::int AS outs,
               (pg.stats->>'strikeouts_pitcher')::int AS k,
               (pg.stats->>'hits_allowed')::int AS h,
               (pg.stats->>'walks_allowed')::int AS bb,
               (pg.stats->>'earned_runs')::int AS er
        FROM player_games pg
        JOIN games g USING (game_id)
        WHERE pg.player_id = :pid
          AND g.game_date < :d
          AND g.sport_code = 'mlb'
          AND (pg.stats->>'batters_faced')::int > 0
        ORDER BY g.game_date, pg.player_game_id
    """
    hist = pd.read_sql(text(sql), engine,
                       params={"pid": pitcher_player_id, "d": game_date})
    features = {"opposing_pitcher_id": int(pitcher_player_id)}
    if hist.empty:
        for w in [5, 10]:
            features[f"pitcher_last_{w}_k_rate"] = 0
            features[f"pitcher_last_{w}_h_per_9"] = 0
            features[f"pitcher_last_{w}_bb_per_9"] = 0
            features[f"pitcher_last_{w}_era"] = 0
            features[f"pitcher_last_{w}_avg_k"] = 0
            features[f"pitcher_last_{w}_avg_outs"] = 0
        return features
    for w in [5, 10]:
        win = hist.iloc[-w:]
        sum_bf = win["bf"].sum()
        sum_outs = win["outs"].sum()
        sum_k = win["k"].sum()
        sum_h = win["h"].sum()
        sum_bb = win["bb"].sum()
        sum_er = win["er"].sum()
        n = len(win)
        features[f"pitcher_last_{w}_k_rate"] = (
            round(sum_k / sum_bf, 4) if sum_bf > 0 else 0
        )
        features[f"pitcher_last_{w}_h_per_9"] = (
            round(sum_h * 27 / sum_outs, 4) if sum_outs > 0 else 0
        )
        features[f"pitcher_last_{w}_bb_per_9"] = (
            round(sum_bb * 27 / sum_outs, 4) if sum_outs > 0 else 0
        )
        features[f"pitcher_last_{w}_era"] = (
            round(sum_er * 27 / sum_outs, 4) if sum_outs > 0 else 0
        )
        features[f"pitcher_last_{w}_avg_k"] = round(sum_k / n, 4)
        features[f"pitcher_last_{w}_avg_outs"] = round(sum_outs / n, 4)
    return features


def build_full_feature_vector(player_id, game_date, season, opposing_pitcher_id=None):
    features = batter_features(player_id, game_date, season)
    if opposing_pitcher_id is not None:
        features.update(pitcher_quality_features(opposing_pitcher_id, game_date))
    return features


if __name__ == "__main__":
    import json
    sql = """
        SELECT pg.player_id, g.game_date, g.season,
               (SELECT pitcher.player_id
                FROM player_games pitcher
                WHERE pitcher.game_id = pg.game_id
                  AND pitcher.team_id = pg.opponent_id
                  AND (pitcher.stats->>'batters_faced')::int > 0
                ORDER BY (pitcher.stats->>'batters_faced')::int DESC
                LIMIT 1) AS opposing_pitcher_id
        FROM player_games pg
        JOIN players p USING (player_id)
        JOIN games g USING (game_id)
        WHERE p.full_name='Shohei Ohtani'
          AND g.sport_code='mlb'
          AND (pg.stats->>'plate_appearances')::int > 0
        ORDER BY g.game_date DESC
        LIMIT 1
    """
    info = pd.read_sql(sql, engine).iloc[0]
    feats = build_full_feature_vector(
        int(info["player_id"]),
        info["game_date"],
        info["season"],
        int(info["opposing_pitcher_id"]) if pd.notna(info["opposing_pitcher_id"]) else None,
    )
    print(f"Built {len(feats)} features for Ohtani on {info['game_date']}")
    print(json.dumps(feats, indent=2))
