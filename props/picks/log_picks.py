"""Generate today's picks across all models and log them to the picks table."""
import json
import time
import requests
from datetime import date
import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from props.utils.db import engine, session_scope
from props.utils.logging import log, configure_logging
from props.picks.predict_today import main as predict_main
from props.models.registry import MODELS
from props.models.prob_calibration import calibrate
from props.models.blend_weights import blend
from props.utils.config import settings
from props.maintenance.migrate import run_migrations


# All pick-suppression policy lives in props.picks.suppression (one documented
# place). Imported here; `_is_out_status` kept as a back-compat alias.
from props.picks.suppression import (
    MIN_EDGE_TO_LOG, MAX_CONFIDENCE, MIN_MINUTES_HARD, MIN_MINUTES_HIGHVAR,
    MIN_LINE_BY_STAT, MAX_LINE_BY_STAT, is_out_status, is_stale_game,
    line_in_range,
)
from props.picks.availability import should_suppress, teammate_bump_from_injury
_is_out_status = is_out_status
_is_stale_game = is_stale_game


def sport_for_model(model_name: str, sport_by_model: dict | None = None) -> str:
    """Resolve a model's sport_code. The combo model (`nba_combo_derived`) isn't
    in the MODELS registry, so a plain `.get(name, "mlb")` mislabeled every NBA
    combo pick (pts_rebs, pts_rebs_asts, …) as MLB — giving it the wrong emoji
    AND the wrong (lenient) per-category cutoff. Fall back to the name prefix."""
    if sport_by_model and model_name in sport_by_model:
        return sport_by_model[model_name]
    for prefix in ("wnba", "nba", "nhl", "mlb"):   # wnba before nba
        if model_name.startswith(prefix):
            return prefix
    return "mlb"


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


def main():
    import argparse
    from datetime import date as _date
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="Target date YYYY-MM-DD (default: today)")
    args, _ = parser.parse_known_args()
    target_date = _date.fromisoformat(args.date) if args.date else _date.today()

    sport_by_model = {m.name: m.sport_code for m in MODELS}
    configure_logging()
    run_migrations()
    # Predict reads are heavy and run against the small remote Railway instance,
    # which can transiently drop the connection under load (E10). Retry the whole
    # predict step on OperationalError — a re-run reliably succeeds — so a daily
    # cron run self-heals instead of producing 0 picks.
    edges = None
    for _attempt in range(3):
        try:
            edges = predict_main(target_date=target_date)
            break
        except OperationalError as e:
            if _attempt == 2:
                raise
            wait = 15 * (_attempt + 1)
            log.warning("predict_retry", attempt=_attempt + 1, wait=wait,
                        error=str(e)[:120])
            time.sleep(wait)
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
    sport_by_model = {m.name: m.sport_code for m in MODELS}
    if "sport_code" not in edges.columns:
        edges = edges.copy()
        edges["sport_code"] = edges["model_name"].map(lambda m: sport_for_model(m, sport_by_model))

    # ── Availability / projected minutes (basketball) ───────────────────────
    # Minutes/DNP swings are the biggest variance source. Project tonight's
    # minutes from each player's latest rolling features and drop likely-DNP /
    # low-minute picks — recency-weighted, so it catches both a player falling
    # out of the rotation and one returning to it (see props.picks.availability).
    # One batch query replaces the old per-player N+1 lookups.
    unavailable: dict[int, str] = {}      # player_id -> reason
    # Tonight's injury context: detect_injury_expansion put the team's lost
    # rotation minutes on edges.injury_flag — used to bump (rescue) plausible
    # rotation players on a depleted team from the minutes suppression.
    flag_map: dict[int, float] = {}
    if "injury_flag" in edges.columns:
        flag_map = {int(p): float(f or 0)
                    for p, f in zip(edges["player_id"], edges["injury_flag"])}
    bball_ids = edges[edges["sport_code"].isin(("nba", "wnba"))]["player_id"].unique().tolist()
    if bball_ids:
        with session_scope() as _s:
            mrows = _s.execute(text("""
                SELECT DISTINCT ON (pg.player_id) pg.player_id, pg.derived
                FROM player_games pg
                JOIN games g ON g.game_id = pg.game_id
                WHERE pg.player_id = ANY(:ids)
                  AND g.sport_code IN ('nba', 'wnba')
                  AND pg.derived IS NOT NULL
                ORDER BY pg.player_id, g.game_date DESC
            """), {"ids": [int(p) for p in bball_ids]}).fetchall()
        for pid, derived in mrows:
            bump = teammate_bump_from_injury(derived or {}, flag_map.get(int(pid), 0.0))
            drop, reason = should_suppress(derived or {}, bump)
            if drop:
                unavailable[int(pid)] = reason
        if unavailable:
            log.info("availability_suppressed", n=len(unavailable))

    # Suppress picks for players currently ruled OUT (or doubtful/IL) — don't log
    # a pick on someone who won't take the floor, which would just void later.
    # player_injuries is name-keyed, so match by name within sport (latest report).
    all_player_ids = edges["player_id"].unique().tolist()
    out_players: set[int] = set()
    if all_player_ids:
        with session_scope() as _si:
            inj_rows = _si.execute(text("""
                SELECT DISTINCT ON (p.player_id) p.player_id, pi.status
                FROM players p
                JOIN player_injuries pi
                  ON pi.sport_code = p.sport_code
                 AND lower(pi.player_name) = lower(p.full_name)
                WHERE p.player_id = ANY(:ids)
                  AND pi.fetched_at > NOW() - INTERVAL '30 hours'
                ORDER BY p.player_id, pi.fetched_at DESC
            """), {"ids": [int(p) for p in all_player_ids]}).fetchall()
        out_players = {int(r[0]) for r in inj_rows if _is_out_status(r[1])}
        if out_players:
            log.info("out_players_suppressed", n=len(out_players))

    # Guard: never log a pick for a game that's already played or dated before
    # the target date. The dashboard hides these, but creating them pollutes the
    # unsettled backlog and can produce "picks" for finished games. A real game
    # is keyed by status (final/live) or a past game_date; today's still-
    # scheduled placeholder games (resolved later by settle) are allowed.
    game_state: dict[int, tuple] = {}
    gid_list = [int(g) for g in edges["game_id"].dropna().unique()]
    if gid_list:
        with session_scope() as _sg:
            for gid, gstatus, gdate in _sg.execute(text("""
                SELECT game_id, status, game_date
                FROM games WHERE game_id = ANY(:ids)
            """), {"ids": gid_list}).all():
                game_state[int(gid)] = (gstatus, gdate)

    inserted = 0
    skipped = 0
    skipped_stale = 0
    skipped_out = 0
    with session_scope() as session:
        for _, row in edges.iterrows():
            if abs(row["edge"]) < MIN_EDGE_TO_LOG:
                skipped += 1
                continue
            # Skip players ruled out / on the IL (would just void as a DNP later).
            if int(row["player_id"]) in out_players:
                skipped_out += 1
                continue
            # Skip games already played (final/live) or dated in the past.
            if _is_stale_game(game_state, row["game_id"], target_date):
                skipped_stale += 1
                continue
            # Skip lines outside the plausible single-game band: trivially low
            # lines (set for returning bench players) and absurdly high ones
            # (multi-game/fantasy cumulatives PrizePicks serves alongside normal
            # lines). See props.picks.suppression for the per-stat bands.
            if not line_in_range(row["stat_type"], row["line_value"]):
                skipped += 1
                continue
            # Skip near-certain picks — no real single-game prop is >97% certain;
            # that high a model prob means the line is a mis-priced multi-game
            # cumulative (e.g. reb avg 6, line 12.5 → P(under) ≈ 100%, meaningless).
            if float(row["model_prob"]) > MAX_CONFIDENCE:
                skipped += 1
                continue
            # Availability: skip likely-DNP / low-projected-minutes basketball picks.
            _reason = unavailable.get(int(row["player_id"]))
            if _reason is not None:
                log.info("suppressed_availability", player=row.get("player_name"),
                         stat=row["stat_type"], reason=_reason)
                skipped += 1
                continue
            mv_id = mv_map[row["model_name"]]
            pred_id = store_prediction_row(
                session, mv_id,
                int(row["player_id"]), int(row["game_id"]),
                row["stat_type"], row["predicted_mean"]
            )
            sport_code = sport_for_model(row["model_name"], sport_by_model)
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
            # Model/market BLEND: combine the raw model prob with the sharp
            # market's no-vig prob for this side, weighted per sport (see
            # props.models.blend_weights — NBA leans market, MLB leans model).
            # market_implied is None when no real line exists, in which case the
            # blend returns the pure model prob — we NEVER blend on a prior. The
            # blended value becomes `model_prob`, so selection, cutoffs,
            # calibration, and display all use it; the raw output is kept in
            # model_prob_raw so the blend weight stays tunable.
            raw_prob     = float(row["model_prob"])
            _mi = row.get("market_implied") if hasattr(row, "get") else None
            market_prob  = (float(_mi) if _mi is not None and pd.notna(_mi) else None)
            model_prob     = round(blend(sport_code, raw_prob, market_prob), 4)
            model_prob_raw = round(raw_prob, 4)
            market_prob_v  = round(market_prob, 4) if market_prob is not None else None
            edge_val     = round(float(row["edge"]), 4)
            line_open, line_movement = _get_line_movement(
                int(row["player_id"]), row["stat_type"],
                sport_code, float(row["line_value"])
            )
            # Half-Kelly for a 2-pick PrizePicks parlay at 3x payout, sized on the
            # RECALIBRATED blended probability (raw is over-confident — see
            # props.models.prob_calibration). f*=(3p-1)/2, half_kelly=f*/2.
            cal_prob     = calibrate(model_prob)
            half_kelly   = round(max(0.0, (3 * cal_prob - 1) / 4), 4)
            _me = row.get("market_edge") if hasattr(row, "get") else None
            market_edge  = (
                round(float(_me), 4)
                if _me is not None and pd.notna(_me)
                else None
            )
            _inj = row.get("injury_flag") if hasattr(row, "get") else None
            injury_flag = (round(float(_inj), 1)
                           if _inj is not None and pd.notna(_inj) else 0.0)
            session.execute(text("""
                INSERT INTO picks (
                    parlay_size, sport_code, player_id, game_id, stat_type,
                    line_id, direction, model_version_id, prediction_id,
                    model_prob, model_prob_raw, market_prob,
                    edge, expected_value, market_edge,
                    line_open, line_movement, injury_flag, picked_at
                ) VALUES (
                    1, :sport, :pid, :gid, :st,
                    :lid, :dir, :mvid, :prid,
                    :mp, :mpr, :mkp, :edge, :ev, :me,
                    :lo, :lm, :inj, NOW()
                )
                ON CONFLICT (player_id, line_id, ((picked_at AT TIME ZONE 'America/Los_Angeles')::date)) DO NOTHING
            """), {
                "sport": sport_code,
                "pid": int(row["player_id"]), "gid": int(row["game_id"]),
                "st": row["stat_type"], "lid": int(row["line_id"]),
                "dir": row["direction"], "mvid": mv_id, "prid": pred_id,
                "mp": model_prob, "mpr": model_prob_raw, "mkp": market_prob_v,
                "edge": edge_val, "ev": half_kelly,
                "me": market_edge,
                "lo": line_open, "lm": line_movement, "inj": injury_flag,
            })
            inserted += 1
    log.info("picks_logged", inserted=inserted, skipped_low_edge=skipped,
             skipped_stale_games=skipped_stale, skipped_out_players=skipped_out)

    if inserted > 0:
        # Digest must match what we actually logged — drop stale-game rows so the
        # alert never lists a pick for an already-played game.
        digest_edges = edges[~edges["game_id"].map(
            lambda g: _is_stale_game(game_state, g, target_date))]
        _send_discord_alert(digest_edges, target_date)


def _send_discord_alert(edges: pd.DataFrame, target_date):
    """Post the recommended slate to Discord AND email — each fires independently
    if configured (webhook / SMTP)."""
    webhook = settings.discord_webhook_url

    # Derive sport_code from model_name using the registry
    sport_by_model = {m.name: m.sport_code for m in MODELS}

    # Align the digest with the dashboard's RECOMMENDED tier: each sport/stat has
    # its own tuned cutoff (per-category #3) instead of a flat 0.70 — so this is
    # the same slate the dashboard surfaces, not a coin-flip band.
    from props.models.category_cutoffs import rec_cutoff
    e = edges.copy()
    e["sport_code"] = e["model_name"].map(lambda m: sport_for_model(m, sport_by_model))
    rec_mask = e.apply(
        lambda r: float(r["model_prob"]) >= rec_cutoff(r["sport_code"], r["stat_type"],
                                                       direction=r["direction"]),
        axis=1,
    )
    top = e[rec_mask].sort_values("model_prob", ascending=False).head(8)
    if top.empty:
        return

    sport_emoji = {"nba": "🏀", "mlb": "⚾", "wnba": "🏀", "nhl": "🏒"}

    fields = []
    for sport_order in ["nba", "wnba", "mlb", "nhl"]:
        sport_picks = top[top["sport_code"] == sport_order]
        if sport_picks.empty:
            continue
        emoji = sport_emoji.get(sport_order, "⚡")
        for _, row in sport_picks.iterrows():
            direction = row["direction"].upper()
            # Show the recalibrated win probability — honest, not over-confident.
            prob = int(round(calibrate(float(row["model_prob"])) * 100))
            market_edge = row.get("market_edge")
            edge_str = f" | +{int(market_edge*100)}% vs mkt" if market_edge and pd.notna(market_edge) else ""
            injury = " ⚠️" if row.get("injury_flag", 0) > 0 else ""
            fields.append({
                "name": f"{emoji} {row['player_name']}",
                "value": f"`{direction} {row['line_value']} {row['stat_type']}` — **{prob}%**{edge_str}{injury}",
                "inline": False,
            })

    # Best 2-pick suggestion — two UNCORRELATED legs (distinct players, and never
    # two legs from the same game+direction, which bust as a block).
    from props.picks.build_parlays import build_diversified_parlay
    par = build_diversified_parlay(top, max_legs=2)
    if len(par) >= 2:
        p1, p2 = par.iloc[0], par.iloc[1]
        joint = round(calibrate(float(p1["model_prob"]))
                      * calibrate(float(p2["model_prob"])) * 100, 1)
        parlay_note = (f"\n**Best 2-pick (uncorrelated):** {p1['player_name']} + {p2['player_name']} "
                       f"— {joint}% joint ({round(joint * 3 / 100, 2)}x EV)")
    else:
        parlay_note = ""

    if webhook:
        payload = {
            "embeds": [{
                "title": f"⚡ prop-edge recommended — {target_date.strftime('%a %b %-d')}",
                "description": f"{len(top)} recommended picks (per-category cutoffs){parlay_note}",
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

    # Email push — same recommended slate, free + reliable phone notification.
    from props.utils.notify import format_slate, send_email
    picks_list = [{
        "sport": r["sport_code"], "player": r["player_name"],
        "direction": r["direction"], "line": r["line_value"],
        "stat": r["stat_type"], "prob": calibrate(float(r["model_prob"])),
    } for _, r in top.iterrows()]
    parlay_list = None
    if len(par) >= 2:
        parlay_list = [{
            "player": pr["player_name"],
            "prob": calibrate(float(pr["model_prob"])),
        } for _, pr in par.iterrows()]
    body = format_slate(picks_list, parlay_list, target_date.strftime("%a %b %-d"))
    send_email(f"⚡ prop-edge slate — {target_date:%a %b %-d}", body)


if __name__ == "__main__":
    main()
