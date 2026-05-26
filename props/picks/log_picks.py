"""Generate today's picks and log them to the picks table for paper-tracking.

Picks are logged with:
- The line that prompted the pick (line_id)
- Model version
- Predicted probability + edge
- All needed for later settlement vs actual outcome
"""
import json
from datetime import date, datetime, timezone
from pathlib import Path
import uuid
import numpy as np
import pandas as pd
import lightgbm as lgb
from scipy import stats as scipy_stats
from sqlalchemy import text

from props.utils.db import engine, session_scope
from props.utils.logging import log, configure_logging
from props.picks.predict_today import (
    load_model, fetch_todays_schedule_with_pitchers,
    resolve_external_to_internal_ids, build_pitcher_features_for_today,
    predict_and_score,
)

MIN_EDGE_TO_LOG = 0.05  # only log picks with at least 5% edge


def ensure_model_version(model_name: str, stat_type: str) -> int:
    """Get or create a model_versions row for this model."""
    with session_scope() as session:
        result = session.execute(text("""
            SELECT model_version_id FROM model_versions
            WHERE sport_code='mlb' AND stat_type=:st AND name=:n
        """), {"st": stat_type, "n": model_name}).first()
        if result:
            return result[0]
        result = session.execute(text("""
            INSERT INTO model_versions (sport_code, stat_type, name, trained_at, notes)
            VALUES ('mlb', :st, :n, NOW(), 'First model trained on 2023-2024 starts')
            RETURNING model_version_id
        """), {"st": stat_type, "n": model_name}).first()
        return result[0]


def load_picks_to_log() -> pd.DataFrame:
    model, meta = load_model()
    today = date.today()
    season = str(today.year)
    games = fetch_todays_schedule_with_pitchers(today)
    games = resolve_external_to_internal_ids(games)
    fdf = build_pitcher_features_for_today(games, today, season, meta["feature_keys"])
    preds = predict_and_score(model, fdf, meta["feature_keys"])

    # Pull standard pitcher_strikeouts lines + game info we need for the pick row
    sql = """
        WITH latest AS (
            SELECT DISTINCT ON (player_id, line_value)
                line_id, player_id, game_id, line_value, snapshot_at
            FROM prop_lines
            WHERE sportsbook='prizepicks' AND sport_code='mlb'
              AND stat_type='strikeouts_pitcher'
              AND line_variant='standard'
              AND player_id = ANY(:ids)
            ORDER BY player_id, line_value, snapshot_at DESC
        )
        SELECT * FROM latest
    """
    lines = pd.read_sql(text(sql), engine, params={"ids": preds["pitcher_id"].tolist()})

    if lines.empty:
        return pd.DataFrame()

    merged = lines.merge(preds, left_on="player_id", right_on="pitcher_id")
    merged["p_over"] = 1 - scipy_stats.poisson.cdf(
        merged["line_value"].astype(int), merged["lambda"]
    )
    merged["p_under"] = 1 - merged["p_over"]
    merged["edge_over"] = merged["p_over"] - 0.5
    merged["edge_under"] = merged["p_under"] - 0.5
    merged["direction"] = np.where(merged["p_over"] > 0.5, "over", "under")
    merged["model_prob"] = np.where(merged["p_over"] > 0.5,
                                    merged["p_over"], merged["p_under"])
    merged["edge"] = merged["model_prob"] - 0.5
    return merged


def store_prediction_row(session, model_version_id: int, player_id: int,
                        game_id: int, predicted_mean: float) -> int:
    """Insert a predictions row and return its ID."""
    result = session.execute(text("""
        INSERT INTO predictions (model_version_id, player_id, game_id, stat_type,
                                 predicted_mean, distribution, dist_params, predicted_at)
        VALUES (:mvid, :pid, :gid, 'strikeouts_pitcher', :mean, 'poisson',
                CAST(:dp AS JSONB), NOW())
        ON CONFLICT (model_version_id, player_id, game_id, stat_type, predicted_at)
        DO NOTHING
        RETURNING prediction_id
    """), {
        "mvid": model_version_id, "pid": player_id, "gid": game_id,
        "mean": round(float(predicted_mean), 4),
        "dp": json.dumps({"lambda": round(float(predicted_mean), 4)}),
    }).first()
    if result:
        return result[0]
    # Already existed -- fetch the existing one
    result = session.execute(text("""
        SELECT prediction_id FROM predictions
        WHERE model_version_id=:mvid AND player_id=:pid AND game_id=:gid
          AND stat_type='strikeouts_pitcher'
        ORDER BY predicted_at DESC LIMIT 1
    """), {"mvid": model_version_id, "pid": player_id, "gid": game_id}).first()
    return result[0]


def log_picks(picks_df: pd.DataFrame, model_version_id: int) -> int:
    """Insert pick rows. Returns count inserted."""
    inserted = 0
    skipped = 0
    with session_scope() as session:
        for _, row in picks_df.iterrows():
            if abs(row["edge"]) < MIN_EDGE_TO_LOG:
                skipped += 1
                continue

            pred_id = store_prediction_row(
                session, model_version_id,
                int(row["player_id"]), int(row["game_id"]),
                row["predicted_mean_k"]
            )

            session.execute(text("""
                INSERT INTO picks (
                    parlay_size, sport_code, player_id, game_id, stat_type,
                    line_id, direction, model_version_id, prediction_id,
                    model_prob, edge, expected_value, picked_at
                ) VALUES (
                    1, 'mlb', :pid, :gid, 'strikeouts_pitcher',
                    :lid, :dir, :mvid, :prid,
                    :mp, :edge, :ev, NOW()
                )
            """), {
                "pid": int(row["player_id"]), "gid": int(row["game_id"]),
                "lid": int(row["line_id"]), "dir": row["direction"],
                "mvid": model_version_id, "prid": pred_id,
                "mp": round(float(row["model_prob"]), 4),
                "edge": round(float(row["edge"]), 4),
                "ev": round(float(row["edge"] * 2), 4),  # rough EV for pickem
            })
            inserted += 1
    return inserted, skipped


def main():
    configure_logging()
    mv_id = ensure_model_version("strikeouts_v1", "strikeouts_pitcher")
    log.info("model_version", model_version_id=mv_id)

    picks_df = load_picks_to_log()
    if picks_df.empty:
        log.warning("no_picks_available")
        return

    print("\n=== Picks to log ===")
    print(picks_df[["pitcher_name", "line_value", "predicted_mean_k",
                    "direction", "model_prob", "edge"]].to_string(index=False))

    inserted, skipped = log_picks(picks_df, mv_id)
    log.info("picks_logged", inserted=inserted, skipped_low_edge=skipped)


if __name__ == "__main__":
    main()
