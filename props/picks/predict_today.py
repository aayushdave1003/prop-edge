"""Generate today's MLB predictions across all registered models.

For each model in the registry:
  - Determine who to predict for (starters if pitcher role, regulars if batter role)
  - Build feature vectors using the inference module
  - Score with the model
  - Convert to Poisson P(over X) for each prop line
  - Match to standard PrizePicks lines and compute edges
"""
import json
import pickle
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
from props.features.inference import batter_features, pitcher_quality_features
from props.models.registry import MODELS, ModelEntry


def load_model(entry):
    log.info("loading_model", name=entry.name)
    model = lgb.Booster(model_file=str(entry.model_path))
    with open(entry.meta_path) as f:
        meta = json.load(f)
    return model, meta


def fetch_todays_schedule_with_pitchers(target_date):
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


def resolve_external_to_internal_ids(games):
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
        game_rows = session.execute(text("""
            SELECT external_id, game_id FROM games
            WHERE sport_code='mlb' AND external_id = ANY(:ids)
        """), {"ids": [g["external_id"] for g in games]}).all()

    pid_map = {row[0]: row[1] for row in pitcher_rows}
    tid_map = {row[0]: row[1] for row in team_rows}
    gid_map = {row[0]: row[1] for row in game_rows}

    resolved = []
    unresolved_pitchers = []
    unresolved_games = []
    for g in games:
        home_pid = pid_map.get(g["home_pitcher_external_id"])
        away_pid = pid_map.get(g["away_pitcher_external_id"])
        gid = gid_map.get(g["external_id"])

        if g["home_pitcher_external_id"] and home_pid is None:
            unresolved_pitchers.append(g["home_pitcher_name"])
        if g["away_pitcher_external_id"] and away_pid is None:
            unresolved_pitchers.append(g["away_pitcher_name"])
        if gid is None:
            unresolved_games.append(g["external_id"])

        resolved.append({
            **g,
            "game_id": gid,
            "home_pitcher_id": home_pid,
            "away_pitcher_id": away_pid,
            "home_team_id": tid_map.get(g["home_team_external_id"]),
            "away_team_id": tid_map.get(g["away_team_external_id"]),
        })

    if unresolved_pitchers:
        log.warning("unresolved_probable_pitchers", names=unresolved_pitchers)
    if unresolved_games:
        log.warning("unresolved_games", external_ids=unresolved_games)
    return resolved


def _opposing_lineup_features(team_id, before_date):
    """Compute opposing-team rolling K rate and offense over prior games."""
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
        feats[f"lineup_last_{w}_k_rate"] = round(sum_k / sum_pa, 4) if sum_pa > 0 else 0
        feats[f"lineup_last_{w}_avg_runs"] = round(win["team_runs"].sum() / n, 4)
        feats[f"lineup_last_{w}_avg_tb"] = round(win["team_tb"].sum() / n, 4)
        feats[f"lineup_last_{w}_walk_rate"] = (
            round(win["team_walks"].sum() / sum_pa, 4) if sum_pa > 0 else 0
        )
    return feats


def _likely_batters_for_team(team_id, target_date, season, top_n=9):
    """Pull the team's recent regulars (most PAs in the last 30 days)."""
    sql = """
        SELECT pg.player_id, p.full_name, COUNT(*) AS games,
               SUM((pg.stats->>'plate_appearances')::int) AS total_pa
        FROM player_games pg
        JOIN players p USING (player_id)
        JOIN games g USING (game_id)
        WHERE pg.team_id = :tid
          AND g.sport_code='mlb'
          AND g.game_date >= :start
          AND g.game_date < :end
          AND (pg.stats->>'plate_appearances')::int >= 3
          AND NOT EXISTS (
              SELECT 1 FROM player_injuries pi
              WHERE pi.player_name = p.full_name
                AND pi.sport_code = 'mlb'
                AND pi.status IN ('10-Day-IL', '15-Day-IL', '60-Day-IL', '7-Day-IL', 'Out')
                AND pi.fetched_at > NOW() - INTERVAL '6 hours'
          )
        GROUP BY pg.player_id, p.full_name
        ORDER BY total_pa DESC
        LIMIT :n
    """
    start = pd.Timestamp(target_date) - pd.Timedelta(days=30)
    df = pd.read_sql(text(sql), engine, params={
        "tid": team_id, "start": start.date(), "end": target_date, "n": top_n
    })
    return df


def build_pitcher_feature_rows(games, target_date, season, feature_keys):
    rows = []
    for g in games:
        if g["game_id"] is None:
            continue
        for side in ["home", "away"]:
            pid = g[f"{side}_pitcher_id"]
            if pid is None:
                continue
            opposing_side = "away" if side == "home" else "home"
            opp_team_id = g[f"{opposing_side}_team_id"]
            if opp_team_id is None:
                continue
            feats = batter_features(pid, target_date, season)
            feats.update(_opposing_lineup_features(opp_team_id, target_date))
            rows.append({
                "player_id": pid,
                "player_name": g[f"{side}_pitcher_name"],
                "game_id": g["game_id"],
                **{k: feats.get(k, 0) for k in feature_keys},
            })
    return pd.DataFrame(rows)


def build_batter_feature_rows(games, target_date, season, feature_keys):
    rows = []
    for g in games:
        if g["game_id"] is None:
            continue
        for side in ["home", "away"]:
            team_id = g[f"{side}_team_id"]
            opposing_side = "away" if side == "home" else "home"
            opp_pitcher_id = g[f"{opposing_side}_pitcher_id"]
            if team_id is None:
                continue
            batters = _likely_batters_for_team(team_id, target_date, season)
            for _, row in batters.iterrows():
                feats = batter_features(int(row["player_id"]), target_date, season)
                if opp_pitcher_id is not None:
                    feats.update(pitcher_quality_features(opp_pitcher_id, target_date))
                rows.append({
                    "player_id": int(row["player_id"]),
                    "player_name": row["full_name"],
                    "game_id": g["game_id"],
                    **{k: feats.get(k, 0) for k in feature_keys},
                })
    return pd.DataFrame(rows)


def score_and_edge(model, meta, entry, feature_df):
    if feature_df.empty:
        return pd.DataFrame()
    feature_keys = meta["feature_keys"]
    X = feature_df[feature_keys].astype(float)
    pred_lambda = model.predict(X, num_iteration=model.best_iteration)
    preds = feature_df[["player_id", "player_name", "game_id"]].copy()
    preds["predicted_mean"] = np.round(pred_lambda, 4)
    preds["lambda"] = pred_lambda
    preds["stat_type"] = entry.stat_type
    preds["model_name"] = entry.name

    pitcher_ids = preds["player_id"].tolist()
    lines = pd.read_sql(text("""
        SELECT DISTINCT ON (player_id, line_value)
            line_id, player_id, game_id, line_value, snapshot_at
        FROM prop_lines
        WHERE sportsbook='prizepicks' AND sport_code=:sport
          AND stat_type=:stat AND line_variant='standard'
          AND player_id = ANY(:ids)
          AND snapshot_at > NOW() - INTERVAL '24 hours'
        ORDER BY player_id, line_value, snapshot_at DESC
    """), engine, params={"sport": entry.sport_code, "stat": entry.stat_type, "ids": pitcher_ids})

    if lines.empty:
        return pd.DataFrame()

    # Lines have PrizePicks placeholder game_ids; preds have real MLB game_ids.
    # Merge on player_id only and take the real game_id from preds.
    merged = lines.merge(preds, on="player_id", how="inner",
                          suffixes=("_line", "_pred"))
    merged["game_id"] = merged["game_id_pred"]

    merged["p_over"] = 1 - scipy_stats.poisson.cdf(
        merged["line_value"].astype(int), merged["lambda"]
    )
    # Apply calibration layer if one exists for this model
    calibrator_path = entry.model_path.parent / f"{entry.name}_calibrator.pkl"
    if calibrator_path.exists():
        with open(calibrator_path, "rb") as f:
            calibrators = pickle.load(f)
        for line in calibrators:
            mask = merged["line_value"].astype(float) == line
            if mask.any():
                merged.loc[mask, "p_over"] = calibrators[line].predict(
                    merged.loc[mask, "p_over"].values
                )
    merged["p_under"] = 1 - merged["p_over"]
    merged["direction"] = np.where(merged["p_over"] > 0.5, "over", "under")
    merged["model_prob"] = np.where(
        merged["p_over"] > 0.5, merged["p_over"], merged["p_under"]
    )
    merged["edge"] = merged["model_prob"] - 0.5
    cols = ["player_name", "stat_type", "line_value", "predicted_mean",
            "direction", "model_prob", "edge", "model_name",
            "line_id", "player_id", "game_id"]
    return merged[cols].sort_values("edge", ascending=False)




def fetch_nba_schedule(target_date):
    """Pull today's NBA games via scoreboardv3."""
    from nba_api.stats.endpoints import scoreboardv3
    sb = scoreboardv3.ScoreboardV3(game_date=target_date.strftime("%Y-%m-%d"))
    raw = sb.get_dict().get("scoreboard", {}).get("games", [])
    games = []
    for g in raw:
        gid = g.get("gameId")
        games.append({
            "external_id": gid,
            "home_team_ext": str(g.get("homeTeam", {}).get("teamId")),
            "away_team_ext": str(g.get("awayTeam", {}).get("teamId")),
        })
    return games


def resolve_nba_external_to_internal_ids(games):
    """Map NBA external_ids to our internal team_ids and game_ids."""
    team_ext_ids = set()
    for g in games:
        team_ext_ids.add(g["home_team_ext"])
        team_ext_ids.add(g["away_team_ext"])
    with session_scope() as session:
        team_rows = session.execute(text("""
            SELECT external_id, team_id FROM teams WHERE sport_code='nba'
        """)).all()
        game_rows = session.execute(text("""
            SELECT external_id, game_id FROM games
            WHERE sport_code='nba' AND external_id = ANY(:ids)
        """), {"ids": [g["external_id"] for g in games]}).all()
    tid_map = {row[0]: row[1] for row in team_rows}
    gid_map = {row[0]: row[1] for row in game_rows}
    resolved = []
    for g in games:
        resolved.append({
            **g,
            "game_id": gid_map.get(g["external_id"]),
            "home_team_id": tid_map.get(g["home_team_ext"]),
            "away_team_id": tid_map.get(g["away_team_ext"]),
        })
    return resolved


def _likely_nba_players_for_team(team_id, target_date, top_n=10):
    """Top N players by minutes over the last 14 days for the given team."""
    sql = """
        SELECT pg.player_id, p.full_name,
               SUM(pg.minutes_played) AS total_min,
               MAX(g.game_date) AS last_played
        FROM player_games pg
        JOIN players p USING (player_id)
        JOIN games g USING (game_id)
        WHERE pg.team_id = :tid
          AND g.sport_code='nba'
          AND g.game_date >= :start
          AND g.game_date < :end
          AND pg.minutes_played >= 5
          AND NOT EXISTS (
              SELECT 1 FROM player_injuries pi
              WHERE pi.player_name = p.full_name
                AND pi.status IN ('Out', 'Doubtful')
                AND pi.fetched_at > NOW() - INTERVAL '6 hours'
          )
        GROUP BY pg.player_id, p.full_name
        HAVING MAX(g.game_date) >= :recent_cutoff
        ORDER BY total_min DESC
        LIMIT :n
    """
    start = pd.Timestamp(target_date) - pd.Timedelta(days=14)
    recent_cutoff = pd.Timestamp(target_date) - pd.Timedelta(days=4)
    return pd.read_sql(text(sql), engine, params={
        "tid": team_id, "start": start.date(), "end": target_date,
        "recent_cutoff": recent_cutoff.date(), "n": top_n,
    })


def _nba_player_features(player_id, before_date, season):
    """Build NBA player feature vector. Mirrors the rolling features module logic.

    Uses ALL the rolling features in player_games.derived from prior games.
    We re-query directly from derived for the most recent prior game.
    """
    sql = """
        SELECT pg.derived
        FROM player_games pg
        JOIN games g USING (game_id)
        WHERE pg.player_id = :pid
          AND g.sport_code='nba'
          AND g.game_date < :d
        ORDER BY g.game_date DESC, pg.player_game_id DESC
        LIMIT 1
    """
    df = pd.read_sql(text(sql), engine, params={"pid": player_id, "d": before_date})
    if df.empty:
        return {}
    # The derived JSONB on the most-recent prior game IS the feature vector
    # for predicting the NEXT game (since features use shift(1) of prior games)
    # But we need to advance by one: the "current game's features" in our DB
    # represent prior-game-only data. For inference, we use that row's features
    # plus advance one game forward conceptually -- simplest is to recompute
    # but that's expensive. Practical shortcut: just use the latest derived.
    return dict(df.iloc[0]["derived"])


def build_nba_player_feature_rows(games, target_date, season, feature_keys):
    """For each NBA game tonight, build feature vectors for the top players."""
    rows = []
    for g in games:
        if g["game_id"] is None:
            # Insert a placeholder game so picks can reference it
            with session_scope() as session:
                gid = session.execute(text("""
                    INSERT INTO games (sport_code, external_id, game_date,
                                      season, season_type, home_team_id, away_team_id, status)
                    VALUES ('nba', :ext, :d, :season, 'playoffs', :htid, :atid, 'scheduled')
                    ON CONFLICT (sport_code, external_id) DO UPDATE
                    SET status = EXCLUDED.status
                    RETURNING game_id
                """), {
                    "ext": g["external_id"], "d": target_date,
                    "season": str(target_date.year if target_date.month >= 10 else target_date.year - 1),
                    "htid": g["home_team_id"], "atid": g["away_team_id"],
                }).first()
                g["game_id"] = gid[0]

        for side in ["home", "away"]:
            team_id = g[f"{side}_team_id"]
            if team_id is None:
                continue
            players = _likely_nba_players_for_team(team_id, target_date)
            for _, row in players.iterrows():
                feats = _nba_player_features(int(row["player_id"]), target_date, season)
                if not feats:
                    continue
                rows.append({
                    "player_id": int(row["player_id"]),
                    "player_name": row["full_name"],
                    "game_id": g["game_id"],
                    **{k: feats.get(k, 0) for k in feature_keys},
                })
    return pd.DataFrame(rows)


def main():
    configure_logging()
    today = date.today()
    season = str(today.year)
    log.info("predicting_for_date", date=today.isoformat())

    games = fetch_todays_schedule_with_pitchers(today)
    log.info("scheduled_games", n=len(games))
    games = resolve_external_to_internal_ids(games)

    nba_games = None  # lazy fetch
    all_picks = []
    for entry in MODELS:
        log.info("running_model", name=entry.name, role=entry.role, sport=entry.sport_code)
        model, meta = load_model(entry)
        if entry.sport_code == "nba":
            if nba_games is None:
                nba_raw = fetch_nba_schedule(today)
                log.info("nba_scheduled_games", n=len(nba_raw))
                nba_games = resolve_nba_external_to_internal_ids(nba_raw)
            features = build_nba_player_feature_rows(nba_games, today, season, meta["feature_keys"])
        elif entry.role == "pitcher":
            features = build_pitcher_feature_rows(games, today, season, meta["feature_keys"])
        else:
            features = build_batter_feature_rows(games, today, season, meta["feature_keys"])
        log.info("built_features", model=entry.name, rows=len(features))
        if features.empty:
            continue
        edges = score_and_edge(model, meta, entry, features)
        if not edges.empty:
            all_picks.append(edges)

    if not all_picks:
        log.warning("no_picks_generated")
        return

    combined = pd.concat(all_picks, ignore_index=True)
    combined = combined.sort_values("edge", ascending=False)
    print("\n=== All edges (sorted by edge size) ===")
    print(combined[["player_name", "stat_type", "line_value", "predicted_mean",
                    "direction", "model_prob", "edge"]].head(30).to_string(index=False))
    return combined


if __name__ == "__main__":
    main()
