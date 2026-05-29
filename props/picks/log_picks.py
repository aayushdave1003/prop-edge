"""Generate today's picks across all models and log them to the picks table."""
import json
from datetime import date
import numpy as np
import pandas as pd
from sqlalchemy import text

from props.utils.db import session_scope
from props.utils.logging import log, configure_logging
from props.picks.predict_today import main as predict_main
from props.models.registry import MODELS


MIN_EDGE_TO_LOG = 0.05


def ensure_model_version(model_name, stat_type):
    with session_scope() as session:
        result = session.execute(text("""
            SELECT model_version_id FROM model_versions
            WHERE sport_code='mlb' AND stat_type=:st AND name=:n
        """), {"st": stat_type, "n": model_name}).first()
        if result:
            return result[0]
        result = session.execute(text("""
            INSERT INTO model_versions (sport_code, stat_type, name, trained_at, notes)
            VALUES ('mlb', :st, :n, NOW(), 'Model trained on 2023-2024')
            RETURNING model_version_id
        """), {"st": stat_type, "n": model_name}).first()
        return result[0]


def store_prediction_row(session, mv_id, player_id, game_id, stat_type, predicted_mean):
    result = session.execute(text("""
        INSERT INTO predictions (model_version_id, player_id, game_id, stat_type,
                                 predicted_mean, distribution, dist_params, predicted_at)
        VALUES (:mvid, :pid, :gid, :st, :mean, 'poisson',
                CAST(:dp AS JSONB), NOW())
        ON CONFLICT (model_version_id, player_id, game_id, stat_type, predicted_at)
        DO NOTHING
        RETURNING prediction_id
    """), {
        "mvid": mv_id, "pid": player_id, "gid": game_id, "st": stat_type,
        "mean": round(float(predicted_mean), 4),
        "dp": json.dumps({"lambda": round(float(predicted_mean), 4)}),
    }).first()
    if result:
        return result[0]
    result = session.execute(text("""
        SELECT prediction_id FROM predictions
        WHERE model_version_id=:mvid AND player_id=:pid AND game_id=:gid
          AND stat_type=:st
        ORDER BY predicted_at DESC LIMIT 1
    """), {"mvid": mv_id, "pid": player_id, "gid": game_id, "st": stat_type}).first()
    return result[0]


def main():
    sport_by_model = {m.name: m.sport_code for m in MODELS}
    configure_logging()
    edges = predict_main()
    if edges is None or edges.empty:
        log.warning("no_edges_to_log")
        return

    # Build model_version_id per stat_type
    mv_map = {}
    for entry in MODELS:
        mv_map[entry.name] = ensure_model_version(entry.name, entry.stat_type)

    inserted = 0
    skipped = 0
    with session_scope() as session:
        for _, row in edges.iterrows():
            if abs(row["edge"]) < MIN_EDGE_TO_LOG:
                skipped += 1
                continue
            mv_id = mv_map[row["model_name"]]
            pred_id = store_prediction_row(
                session, mv_id,
                int(row["player_id"]), int(row["game_id"]),
                row["stat_type"], row["predicted_mean"]
            )
            sport_code = sport_by_model.get(row["model_name"], "mlb")
            # Dedup guard: PrizePicks re-snapshots create new line_ids for the same
            # logical line (same value), and the existing unique index keys on
            # line_id so it can't catch this. Check by (player, stat, direction,
            # line_value, date) and skip if an equivalent pick already exists.
            already = session.execute(text("""
                SELECT 1 FROM picks pk
                JOIN prop_lines pl ON pl.line_id = pk.line_id
                WHERE pk.player_id = :pid
                  AND pk.stat_type = :st
                  AND pk.direction = :dir
                  AND pl.line_value = :lv
                  AND (pk.picked_at AT TIME ZONE 'America/Los_Angeles')::date
                      = (NOW() AT TIME ZONE 'America/Los_Angeles')::date
                LIMIT 1
            """), {
                "pid": int(row["player_id"]),
                "st": row["stat_type"],
                "dir": row["direction"],
                "lv": float(row["line_value"]),
            }).first()
            if already:
                skipped += 1
                continue
            session.execute(text("""
                INSERT INTO picks (
                    parlay_size, sport_code, player_id, game_id, stat_type,
                    line_id, direction, model_version_id, prediction_id,
                    model_prob, edge, expected_value, picked_at
                ) VALUES (
                    1, :sport, :pid, :gid, :st,
                    :lid, :dir, :mvid, :prid,
                    :mp, :edge, :ev, NOW()
                )
                ON CONFLICT (player_id, line_id, ((picked_at AT TIME ZONE 'America/Los_Angeles')::date)) DO NOTHING
            """), {
                "sport": sport_code,
                "pid": int(row["player_id"]), "gid": int(row["game_id"]),
                "st": row["stat_type"], "lid": int(row["line_id"]),
                "dir": row["direction"], "mvid": mv_id, "prid": pred_id,
                "mp": round(float(row["model_prob"]), 4),
                "edge": round(float(row["edge"]), 4),
                "ev": round(float(row["edge"] * 2), 4),
            })
            inserted += 1
    log.info("picks_logged", inserted=inserted, skipped_low_edge=skipped)


if __name__ == "__main__":
    main()
