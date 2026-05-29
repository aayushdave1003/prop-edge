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
from props.ingest.game_odds import fetch_nba_game_context, map_context_to_game_ids
from props.picks.predict_game import predict_games, print_game_predictions
from props.picks.predict_mlb_game import predict_mlb_games, print_mlb_game_predictions
from props.ingest.market_odds import build_market_probs
from props.picks.build_parlays import (
    build_correlated_parlays, print_parlay_recommendations,
    build_slate, print_slate,
)


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
                AND pi.fetched_at > NOW() - INTERVAL '30 hours'
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
    extra_cols = [c for c in ["team_id"] if c in feature_df.columns]
    preds = feature_df[["player_id", "player_name", "game_id"] + extra_cols].copy()
    preds["predicted_mean"] = np.round(pred_lambda, 4)
    preds["lambda"] = pred_lambda
    preds["stat_type"] = entry.stat_type
    preds["model_name"] = entry.name

    pitcher_ids = preds["player_id"].tolist()
    # Use only lines from the most recent scrape cycle (within 2h of latest snapshot).
    # The 24h window accumulates every hourly scrape and includes stale/removed lines.
    lines = pd.read_sql(text("""
        WITH latest AS (
            SELECT MAX(snapshot_at) AS max_snap
            FROM prop_lines
            WHERE sportsbook='prizepicks' AND sport_code=:sport
              AND stat_type=:stat AND line_variant='standard'
              AND player_id = ANY(:ids)
        )
        SELECT DISTINCT ON (pl.player_id, pl.line_value)
            pl.line_id, pl.player_id, pl.game_id, pl.line_value, pl.snapshot_at
        FROM prop_lines pl, latest
        WHERE pl.sportsbook='prizepicks' AND pl.sport_code=:sport
          AND pl.stat_type=:stat AND pl.line_variant='standard'
          AND pl.player_id = ANY(:ids)
          AND pl.snapshot_at >= latest.max_snap - INTERVAL '2 hours'
        ORDER BY pl.player_id, pl.line_value, pl.snapshot_at DESC
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
    # Apply calibration. New format: {"global": iso} covers all lines.
    # Legacy format: {9.5: iso, ...} only covers standard lines.
    calibrator_path = entry.model_path.parent / f"{entry.name}_calibrator.pkl"
    if calibrator_path.exists():
        with open(calibrator_path, "rb") as f:
            calibrators = pickle.load(f)
        if "global" in calibrators:
            merged["p_over"] = calibrators["global"].predict(merged["p_over"].values)
        else:
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

    # Drop low-information hits lines. A 0.5 hits line is "does the batter get
    # any hit at all" -- almost everyone clears it, so the model trivially calls
    # OVER and it's a coin flip after payout. Only take hits lines >= 1.5 where
    # the model's distribution actually carries signal.
    if entry.stat_type == "hits":
        before = len(merged)
        merged = merged[merged["line_value"].astype(float) >= 1.5]
        log.info("hits_line_floor_applied", dropped=before - len(merged),
                 kept=len(merged))

    cols = ["player_name", "stat_type", "line_value", "predicted_mean",
            "direction", "model_prob", "edge", "model_name",
            "line_id", "player_id", "game_id"]
    if "team_id" in merged.columns:
        cols.append("team_id")
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


def detect_injury_expansion(nba_games: list, target_date) -> dict:
    """Return {player_id: injured_teammate_avg_min} for players whose key teammate is out.

    A player is flagged when a teammate averaging 15+ minutes over the last
    14 days is listed Out or Doubtful tonight. The value is the sum of avg
    minutes that injured teammates are losing — a proxy for role expansion.
    """
    with session_scope() as session:
        injured_rows = session.execute(text("""
            SELECT DISTINCT ON (player_name)
                player_name, team_name, status
            FROM player_injuries
            WHERE status IN ('Out', 'Doubtful')
              AND sport_code = 'nba'
              AND fetched_at > NOW() - INTERVAL '30 hours'
            ORDER BY player_name, fetched_at DESC
        """)).all()

    if not injured_rows:
        return {}

    injured_names = {r[0].lower() for r in injured_rows}
    start = (pd.Timestamp(target_date) - pd.Timedelta(days=14)).date()
    flags: dict[int, float] = {}

    for g in nba_games:
        game_id = g.get("game_id")
        if not game_id:
            continue
        for side in ["home", "away"]:
            team_id = g.get(f"{side}_team_id")
            if not team_id:
                continue

            roster = pd.read_sql(text("""
                SELECT p.player_id, p.full_name,
                       AVG(pg.minutes_played) AS avg_min
                FROM player_games pg
                JOIN players p USING (player_id)
                JOIN games gm USING (game_id)
                WHERE pg.team_id = :tid
                  AND gm.sport_code = 'nba'
                  AND gm.game_date >= :start
                  AND gm.game_date < :end
                  AND pg.minutes_played >= 5
                GROUP BY p.player_id, p.full_name
                HAVING COUNT(*) >= 3
            """), engine, params={"tid": team_id, "start": start, "end": target_date})

            if roster.empty:
                continue

            injured_min_out = sum(
                row["avg_min"]
                for _, row in roster.iterrows()
                if row["full_name"].lower() in injured_names and row["avg_min"] >= 15
            )

            if injured_min_out >= 15:
                healthy_pids = [
                    int(row["player_id"])
                    for _, row in roster.iterrows()
                    if row["full_name"].lower() not in injured_names
                ]
                for pid in healthy_pids:
                    flags[pid] = round(injured_min_out, 1)

    if flags:
        log.info("injury_expansion_flags", players=len(flags))
    return flags


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
                AND pi.fetched_at > NOW() - INTERVAL '30 hours'
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
                    "team_id": team_id,
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

    # MLB game winner predictions (runs before prop models)
    mlb_games_with_ids = [g for g in games if g.get("game_id") and g.get("home_team_id")]
    if mlb_games_with_ids:
        with session_scope() as _s:
            mlb_team_rows = _s.execute(text(
                "SELECT team_id, COALESCE(city || ' ', '') || name FROM teams WHERE sport_code='mlb'"
            )).all()
        mlb_team_names = {r[0]: r[1] for r in mlb_team_rows}
        mlb_preds = predict_mlb_games(mlb_games_with_ids, today)
        print_mlb_game_predictions(mlb_preds, mlb_team_names)

    nba_games = None  # lazy fetch
    nba_game_ctx_map = {}
    all_picks = []
    for entry in MODELS:
        log.info("running_model", name=entry.name, role=entry.role, sport=entry.sport_code)
        model, meta = load_model(entry)
        if entry.sport_code == "nba":
            if nba_games is None:
                nba_raw = fetch_nba_schedule(today)
                log.info("nba_scheduled_games", n=len(nba_raw))
                nba_games = resolve_nba_external_to_internal_ids(nba_raw)
                # Game context + winner predictions up front
                espn_raw = fetch_nba_game_context(today)
                nba_game_ctx_map = map_context_to_game_ids(espn_raw, nba_games)
                with session_scope() as _s:
                    team_rows = _s.execute(text(
                        "SELECT team_id, city || ' ' || name FROM teams WHERE sport_code='nba'"
                    )).all()
                team_names = {r[0]: r[1] for r in team_rows}
                game_preds = predict_games(nba_games, today, nba_game_ctx_map)
                print_game_predictions(game_preds, team_names)
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

    # --- Annotate with game context (totals / implied team totals) ---
    if nba_games:
        game_ctx_map = nba_game_ctx_map  # already fetched above

        def _get_total(row):
            ctx = game_ctx_map.get(row["game_id"])
            return ctx["total"] if ctx else None

        def _get_implied(row):
            ctx = game_ctx_map.get(row["game_id"])
            if not ctx:
                return None
            tid = row.get("team_id")
            if tid == ctx.get("home_team_id"):
                return ctx.get("implied_home")
            if tid == ctx.get("away_team_id"):
                return ctx.get("implied_away")
            return ctx.get("total")

        combined["game_total"]          = combined.apply(_get_total, axis=1)
        combined["implied_team_total"]  = combined.apply(_get_implied, axis=1)
    else:
        combined["game_total"]         = None
        combined["implied_team_total"] = None

    # --- Market-edge: compare model prob vs sharp book no-vig midpoint ---
    market_probs = build_market_probs(today)
    if market_probs:
        def _market_implied(row):
            key = (row["player_name"].lower().strip(), row["stat_type"],
                   float(row["line_value"]))
            p = market_probs.get(key)
            if p is None:
                return None
            # Return prob from the perspective of the picked direction
            return p if row["direction"] == "over" else 1.0 - p

        combined["market_implied"] = combined.apply(_market_implied, axis=1)
        # market_edge = model advantage over what the sharp market prices in.
        # Falls back to model_prob - 0.5 (flat pick'em baseline) when no market
        # line is available for that player/stat/line combo.
        combined["market_edge"] = combined.apply(
            lambda r: (
                r["model_prob"] - r["market_implied"]
                if pd.notna(r["market_implied"])
                else r["model_prob"] - 0.5
            ),
            axis=1,
        ).round(4)
        n_matched = combined["market_implied"].notna().sum()
        log.info("market_edge_computed", matched=int(n_matched),
                 total=len(combined))
    else:
        combined["market_implied"] = None
        combined["market_edge"]    = combined["edge"]   # identical to model edge

    # Half-Kelly fraction for a 2-pick parlay at 3x payout (net odds = 2):
    #   f* = (2p_joint - (1-p_joint)) / 2 = (3p - 1) / 2
    #   half_kelly = f* / 2 = (3p - 1) / 4
    # Where p is the individual leg model_prob (assumes pairing with an equal-quality leg).
    # Negative values → below 2-pick breakeven (57.7%), clip to 0.
    combined["kelly_fraction"] = (
        combined["model_prob"].apply(lambda p: max(0.0, round((3 * p - 1) / 4, 4)))
    )

    # Re-rank by market_edge: best picks first
    combined = combined.sort_values("market_edge", ascending=False)

    # --- Annotate with injury expansion flags ---
    if nba_games:
        injury_flags = detect_injury_expansion(nba_games, today)
        combined["injury_flag"] = combined["player_id"].map(
            lambda pid: injury_flags.get(int(pid), 0)
        )
    else:
        combined["injury_flag"] = 0

    # --- Print enriched pick table ---
    has_market = combined["market_implied"].notna().any()
    edge_label  = "market_edge" if has_market else "edge"
    print(f"\n=== All picks (ranked by {edge_label}) ===")
    display_cols = ["player_name", "stat_type", "line_value", "predicted_mean",
                    "direction", "model_prob", "market_implied", "market_edge",
                    "kelly_fraction", "game_total", "implied_team_total", "injury_flag"]
    print(
        combined[[c for c in display_cols if c in combined.columns]]
        .head(30)
        .to_string(index=False)
    )

    # Highlight picks below 2-pick breakeven (57.7%) as a warning
    below_breakeven = combined[combined["model_prob"] < 0.577]
    if not below_breakeven.empty:
        print(f"\n  ⚠  {len(below_breakeven)} picks below 2-pick breakeven (57.7%) — "
              "avoid as parlay legs unless edge vs market is very strong")

    # --- Slate: committed picks card ---
    nba_picks = combined[combined["model_name"].str.startswith("nba")].copy()
    if not nba_picks.empty:
        slate = build_slate(nba_picks)
        print_slate(slate, title=f"TODAY'S PICKS — {today.strftime('%a %b %-d')}")

    # --- Full ranked combo list (detailed analysis) ---
    if not nba_picks.empty:
        combos = build_correlated_parlays(nba_picks)
        print_parlay_recommendations(combos)

    return combined


if __name__ == "__main__":
    main()
