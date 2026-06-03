"""Generate today's picks across all models and log them to the picks table."""
import json
import requests
from datetime import date
import numpy as np
import pandas as pd
from sqlalchemy import text

from props.utils.db import engine, session_scope
from props.utils.logging import log, configure_logging
from props.picks.predict_today import main as predict_main
from props.models.registry import MODELS
from props.utils.config import settings


MIN_EDGE_TO_LOG = 0.05  # model_prob > 0.55; 2-pick breakeven is 57.7% — warn below that


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


def _ensure_market_edge_column():
    """Add market_edge column to picks if it doesn't exist yet."""
    with session_scope() as session:
        session.execute(text("""
            ALTER TABLE picks ADD COLUMN IF NOT EXISTS market_edge numeric(6,4)
        """))


def main():
    import argparse
    from datetime import date as _date
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="Target date YYYY-MM-DD (default: today)")
    args, _ = parser.parse_known_args()
    target_date = _date.fromisoformat(args.date) if args.date else _date.today()

    sport_by_model = {m.name: m.sport_code for m in MODELS}
    configure_logging()
    _ensure_market_edge_column()
    edges = predict_main(target_date=target_date)
    if edges is None or edges.empty:
        log.warning("no_edges_to_log")
        return

    # Build model_version_id per stat_type
    mv_map = {}
    for entry in MODELS:
        mv_map[entry.name] = ensure_model_version(entry.name, entry.stat_type)

    # Fetch min_stddev_last_10 for all NBA players in this slate to suppress
    # high-variance bench picks (e.g. Carter Bryant 83% on 2.5 pts)
    HIGH_VAR_THRESHOLD = 7.0
    sport_by_model = {m.name: m.sport_code for m in MODELS}
    if "sport_code" not in edges.columns:
        edges = edges.copy()
        edges["sport_code"] = edges["model_name"].map(lambda m: sport_by_model.get(m, "mlb"))
    nba_player_ids = edges[edges["sport_code"] == "nba"]["player_id"].unique().tolist()
    high_var_players = set()
    if nba_player_ids:
        with session_scope() as _s:
            rows_hv = _s.execute(text("""
                SELECT DISTINCT ON (pg.player_id) pg.player_id,
                       (pg.derived->>'min_stddev_last_10')::float AS stddev
                FROM player_games pg
                JOIN games g ON g.game_id = pg.game_id
                WHERE pg.player_id = ANY(:ids)
                  AND g.sport_code = 'nba'
                  AND pg.derived->>'min_stddev_last_10' IS NOT NULL
                ORDER BY pg.player_id, g.game_date DESC
            """), {"ids": [int(p) for p in nba_player_ids]}).fetchall()
        # Only suppress if high variance AND low avg minutes — catches bench DNP risks
        # (Carter Bryant: stddev=8, avg=7min) without suppressing starters with OT variance
        # (Wemby: stddev=9.5, avg=33min — keep)
        high_var_players = set()
        for r in rows_hv:
            pid, stddev = r[0], r[1]
            if stddev and stddev > HIGH_VAR_THRESHOLD:
                # Also check avg minutes — don't suppress rotation players
                with session_scope() as _s2:
                    avg_row = _s2.execute(text("""
                        SELECT (pg.derived->>'last_10_avg_minutes')::float
                        FROM player_games pg JOIN games g ON g.game_id = pg.game_id
                        WHERE pg.player_id = :pid AND g.sport_code = 'nba'
                        ORDER BY g.game_date DESC LIMIT 1
                    """), {"pid": pid}).first()
                avg_min = float(avg_row[0]) if avg_row and avg_row[0] else 0
                if avg_min < 18:  # only suppress true bench players
                    high_var_players.add(pid)
        if high_var_players:
            log.info("high_variance_players_suppressed", n=len(high_var_players))

    inserted = 0
    skipped = 0
    with session_scope() as session:
        for _, row in edges.iterrows():
            if abs(row["edge"]) < MIN_EDGE_TO_LOG:
                skipped += 1
                continue
            # Suppress NBA bench players with wildly inconsistent minutes
            if row.get("sport_code") == "nba" and int(row["player_id"]) in high_var_players:
                log.info("suppressed_high_var_pick", player=row.get("player_name"),
                         stat=row["stat_type"])
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
            model_prob   = round(float(row["model_prob"]), 4)
            edge_val     = round(float(row["edge"]), 4)
            # Half-Kelly for a 2-pick PrizePicks parlay at 3x payout:
            #   f* = (3p - 1) / 2,  half_kelly = f* / 2 = (3p - 1) / 4
            # Replaces the old (incorrect) edge * 2 formula.
            half_kelly   = round(max(0.0, (3 * model_prob - 1) / 4), 4)
            _me = row.get("market_edge") if hasattr(row, "get") else None
            market_edge  = (
                round(float(_me), 4)
                if _me is not None and pd.notna(_me)
                else None
            )
            session.execute(text("""
                INSERT INTO picks (
                    parlay_size, sport_code, player_id, game_id, stat_type,
                    line_id, direction, model_version_id, prediction_id,
                    model_prob, edge, expected_value, market_edge, picked_at
                ) VALUES (
                    1, :sport, :pid, :gid, :st,
                    :lid, :dir, :mvid, :prid,
                    :mp, :edge, :ev, :me, NOW()
                )
                ON CONFLICT (player_id, line_id, ((picked_at AT TIME ZONE 'America/Los_Angeles')::date)) DO NOTHING
            """), {
                "sport": sport_code,
                "pid": int(row["player_id"]), "gid": int(row["game_id"]),
                "st": row["stat_type"], "lid": int(row["line_id"]),
                "dir": row["direction"], "mvid": mv_id, "prid": pred_id,
                "mp": model_prob, "edge": edge_val, "ev": half_kelly,
                "me": market_edge,
            })
            inserted += 1
    log.info("picks_logged", inserted=inserted, skipped_low_edge=skipped)

    if inserted > 0:
        _send_discord_alert(edges, target_date)


def _send_discord_alert(edges: pd.DataFrame, target_date):
    """Post top picks to Discord. Fires only if webhook is configured."""
    webhook = settings.discord_webhook_url
    if not webhook:
        return

    # Derive sport_code from model_name using the registry
    sport_by_model = {m.name: m.sport_code for m in MODELS}

    ALERT_THRESHOLD = 0.65
    top = (
        edges[edges["model_prob"] >= ALERT_THRESHOLD]
        .sort_values("model_prob", ascending=False)
        .head(8)
    )
    if top.empty:
        return

    sport_emoji = {"nba": "🏀", "mlb": "⚾", "wnba": "🏀", "nhl": "🏒"}

    # Add sport_code column for grouping
    top = top.copy()
    top["sport_code"] = top["model_name"].map(lambda m: sport_by_model.get(m, "mlb"))

    fields = []
    for sport_order in ["nba", "wnba", "mlb", "nhl"]:
        sport_picks = top[top["sport_code"] == sport_order]
        if sport_picks.empty:
            continue
        emoji = sport_emoji.get(sport_order, "⚡")
        for _, row in sport_picks.iterrows():
            direction = row["direction"].upper()
            prob = int(round(row["model_prob"] * 100))
            market_edge = row.get("market_edge")
            edge_str = f" | +{int(market_edge*100)}% vs mkt" if market_edge and pd.notna(market_edge) else ""
            injury = " ⚠️" if row.get("injury_flag", 0) > 0 else ""
            fields.append({
                "name": f"{emoji} {row['player_name']}",
                "value": f"`{direction} {row['line_value']} {row['stat_type']}` — **{prob}%**{edge_str}{injury}",
                "inline": False,
            })

    # Best 2-pick suggestion
    if len(top) >= 2:
        p1, p2 = top.iloc[0], top.iloc[1]
        joint = round(p1["model_prob"] * p2["model_prob"] * 100, 1)
        parlay_note = (f"\n**Best 2-pick:** {p1['player_name']} + {p2['player_name']} "
                       f"— {joint}% joint ({round(joint * 3 / 100, 2)}x EV)")
    else:
        parlay_note = ""

    payload = {
        "embeds": [{
            "title": f"⚡ prop-edge picks — {target_date.strftime('%a %b %-d')}",
            "description": f"{len(top)} picks ≥ 65% confidence{parlay_note}",
            "color": 0x5932d9,
            "fields": fields,
            "footer": {"text": "prop-edge • auto-generated"},
        }]
    }

    try:
        r = requests.post(webhook, json=payload, timeout=10)
        if r.status_code in (200, 204):
            log.info("discord_alert_sent", picks=len(top))
        else:
            log.warning("discord_alert_failed", status=r.status_code)
    except Exception as e:
        log.warning("discord_alert_error", error=str(e))


if __name__ == "__main__":
    main()
