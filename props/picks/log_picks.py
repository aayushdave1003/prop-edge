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

# Minimum line value per stat — filters trivially low lines (OVER 2.5 pts etc.)
# that are set low for injured/bench players returning and carry no real signal.
# Maximum line value per stat — filters multi-game cumulative lines that
# PrizePicks sometimes serves alongside standard single-game lines.
# A WNBA player scoring 35+ in one game is extremely rare; 37 is impossible as a
# normal prop. These inflated values are 2-game or fantasy-format accumulations.
MAX_LINE_BY_STAT = {
    # Basketball (NBA/WNBA/NHL share keys — use the NBA max as ceiling)
    "points":             55.0,
    "rebounds":           22.0,
    "assists":            20.0,
    "threes_made":        10.0,
    "pts_rebs_asts":      80.0,
    "pts_rebs":           60.0,
    "pts_asts":           60.0,
    "rebs_asts":          35.0,
    "blocks":              8.0,
    "steals":              8.0,
    "blocks_steals":      12.0,
    # MLB
    "strikeouts_pitcher": 17.0,
    "home_runs":           4.0,
    "hits":                6.0,
    "rbis":               10.0,
    "total_bases":        14.0,
    # NHL
    "goals":               5.0,
    "saves":              50.0,
}

MIN_LINE_BY_STAT = {
    # NBA / WNBA
    "points":             5.0,
    "rebounds":           3.0,
    "assists":            1.5,
    "threes_made":        0.5,
    "pts_rebs_asts":     15.0,
    "pts_rebs":          10.0,
    "pts_asts":          10.0,
    "rebs_asts":          5.0,
    "blocks":             0.5,
    "steals":             0.5,
    "blocks_steals":      1.0,
    # MLB
    "strikeouts_pitcher": 2.5,
    "hits":               1.5,
    "rbis":               0.5,
    "total_bases":        1.5,
    "home_runs":          0.5,
    # NHL
    "goals":              0.5,
    "saves":             15.0,
}


def ensure_model_version(model_name, stat_type, sport_code="mlb"):
    with session_scope() as session:
        result = session.execute(text("""
            SELECT model_version_id FROM model_versions WHERE name=:n
        """), {"n": model_name}).first()
        if result:
            return result[0]
        result = session.execute(text("""
            INSERT INTO model_versions (sport_code, stat_type, name, trained_at, notes)
            VALUES (:sport, :st, :n, NOW(), 'auto-registered')
            RETURNING model_version_id
        """), {"sport": sport_code, "st": stat_type, "n": model_name}).first()
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


def _ensure_line_movement_columns():
    """Add line_open and line_movement columns to picks if missing."""
    with session_scope() as session:
        for col, type_ in [("line_open", "NUMERIC(8,3)"), ("line_movement", "NUMERIC(6,3)")]:
            try:
                session.execute(text(f"ALTER TABLE picks ADD COLUMN IF NOT EXISTS {col} {type_}"))
            except Exception:
                pass


def _get_line_movement(player_id: int, stat_type: str, sport_code: str,
                        current_line: float) -> tuple:
    """Return (line_open, line_movement) for this player/stat today."""
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT line_value, snapshot_at
            FROM prop_lines
            WHERE player_id = :pid
              AND stat_type = :stat
              AND sport_code = :sport
              AND sportsbook = 'prizepicks'
              AND line_variant = 'standard'
              AND snapshot_at >= NOW() - INTERVAL '24 hours'
            ORDER BY snapshot_at ASC
            LIMIT 1
        """), {"pid": player_id, "stat": stat_type, "sport": sport_code}).first()
    if row:
        line_open = float(row[0])
        movement  = round(float(current_line) - line_open, 3)
        return line_open, movement
    return None, None


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
    _ensure_line_movement_columns()
    edges = predict_main(target_date=target_date)
    if edges is None or edges.empty:
        log.warning("no_edges_to_log")
        return

    # Build model_version_id per stat_type
    mv_map = {}
    for entry in MODELS:
        mv_map[entry.name] = ensure_model_version(entry.name, entry.stat_type, entry.sport_code)
    # Combo stat model isn't in registry — register it on demand
    mv_map["nba_combo_derived"] = ensure_model_version("nba_combo_derived", "combo", "nba")

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
                with session_scope() as _s2:
                    avg_row = _s2.execute(text("""
                        SELECT (pg.derived->>'last_10_avg_minutes')::float
                        FROM player_games pg JOIN games g ON g.game_id = pg.game_id
                        WHERE pg.player_id = :pid AND g.sport_code = 'nba'
                        ORDER BY g.game_date DESC LIMIT 1
                    """), {"pid": pid}).first()
                avg_min = float(avg_row[0]) if avg_row and avg_row[0] else 0
                if avg_min < 18:
                    high_var_players.add(pid)
        if high_var_players:
            log.info("high_variance_players_suppressed", n=len(high_var_players))

    # Hard minimum minutes floor: skip any player averaging < 12 min regardless of edge.
    # Catches bench DNP risks that slip past the high-variance filter.
    low_minute_players = set()
    all_player_ids = edges["player_id"].unique().tolist()
    if all_player_ids:
        with session_scope() as _s:
            rows_min = _s.execute(text("""
                SELECT DISTINCT ON (pg.player_id)
                       pg.player_id,
                       (pg.derived->>'last_10_avg_minutes')::float AS avg_min,
                       g.sport_code
                FROM player_games pg
                JOIN games g ON g.game_id = pg.game_id
                WHERE pg.player_id = ANY(:ids)
                  AND pg.derived->>'last_10_avg_minutes' IS NOT NULL
                ORDER BY pg.player_id, g.game_date DESC
            """), {"ids": [int(p) for p in all_player_ids]}).fetchall()
        for r in rows_min:
            pid, avg_min, sc = r[0], r[1], r[2]
            if avg_min is not None and avg_min < 12:
                low_minute_players.add(pid)
        if low_minute_players:
            log.info("low_minute_players_suppressed", n=len(low_minute_players))

    inserted = 0
    skipped = 0
    with session_scope() as session:
        for _, row in edges.iterrows():
            if abs(row["edge"]) < MIN_EDGE_TO_LOG:
                skipped += 1
                continue
            # Skip trivially low lines (OVER 2.5 pts, OVER 2.5 reb, etc.)
            min_line = MIN_LINE_BY_STAT.get(row["stat_type"], 0)
            if float(row["line_value"]) < min_line:
                skipped += 1
                continue
            # Skip absurdly high lines — these are multi-game cumulative totals or
            # fantasy-format lines that PrizePicks serves alongside normal lines.
            max_line = MAX_LINE_BY_STAT.get(row["stat_type"], float("inf"))
            if float(row["line_value"]) > max_line:
                skipped += 1
                continue
            # Skip near-100% confidence picks — no real single-game prop should
            # be >97% certain. This catches multi-game lines where the model's
            # single-game lambda is tiny relative to the inflated line (e.g. reb
            # avg 6, line 12.5 → P(under) ≈ 100% but the pick is meaningless).
            if float(row["model_prob"]) > 0.97:
                skipped += 1
                continue
            # Suppress players averaging < 12 min (DNP risk)
            if int(row["player_id"]) in low_minute_players:
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
            line_open, line_movement = _get_line_movement(
                int(row["player_id"]), row["stat_type"],
                sport_code, float(row["line_value"])
            )
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
                    model_prob, edge, expected_value, market_edge,
                    line_open, line_movement, picked_at
                ) VALUES (
                    1, :sport, :pid, :gid, :st,
                    :lid, :dir, :mvid, :prid,
                    :mp, :edge, :ev, :me,
                    :lo, :lm, NOW()
                )
                ON CONFLICT (player_id, line_id, ((picked_at AT TIME ZONE 'America/Los_Angeles')::date)) DO NOTHING
            """), {
                "sport": sport_code,
                "pid": int(row["player_id"]), "gid": int(row["game_id"]),
                "st": row["stat_type"], "lid": int(row["line_id"]),
                "dir": row["direction"], "mvid": mv_id, "prid": pred_id,
                "mp": model_prob, "edge": edge_val, "ev": half_kelly,
                "me": market_edge,
                "lo": line_open, "lm": line_movement,
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
