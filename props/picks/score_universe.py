"""Score the FULL prop universe and persist it to ``scored_props``.

``predict_today`` only builds feature rows for the *likely* players on each
team (top-N by recent minutes / PAs) and ``log_picks`` then keeps only the
high-edge subset — so the dashboard's "Build your own parlay" can only offer
the ~28 logged picks. This step closes that gap: for every modeled (sport,
stat) it scores EVERY player who has a current PrizePicks standard line on an
upcoming real game and upserts the full scored set (recommended or not) into
``scored_props``, giving the dashboard a model EV for any player.

Design — maximal reuse of the existing pipeline so stored probabilities are
*identical* to what gets logged:
  * Feature rows are built with predict_today's own builders. For WNBA/NHL the
    existing ``build_derived_player_feature_rows`` already scans the full
    prop_lines universe (not a likely-player subset), so it's reused verbatim.
    For NBA/MLB the likely-player builders are reused, then *augmented* with a
    universe pass that adds any line-carrying player they missed, so coverage is
    the whole standard-line board rather than just rotation regulars.
  * Scoring + line matching + isotonic calibration go through the unchanged
    ``score_and_edge`` (same Poisson/binary math, same per-model calibrator,
    same 2h fresh-line window, same hits>=1.5 floor).
  * The stored ``model_prob`` is then run through the SAME post-processing
    log_picks applies before it writes ``picks.model_prob`` — per-direction
    calibration (``calibrate_dir``) then the per-sport model/market ``blend``
    — so a scored prop and a logged pick for the same line carry the same
    number, and the dashboard's display ``calibrate()`` stays consistent across
    both. (``calibrate()`` is applied at *display* time, not stored, matching
    picks.)

Read-only on every existing table; the only writes are UPSERTs into
``scored_props``.

Run standalone (PROD):  DATABASE_URL=$RAILWAY_DATABASE_URL \
                          python -m props.picks.score_universe --date 2026-06-17
"""
import argparse
from datetime import date

import pandas as pd
from sqlalchemy import text

from props.utils.db import engine, session_scope, db_banner
from props.utils.logging import log, configure_logging
from props.models.registry import MODELS
from props.models.dir_calibration import calibrate_dir
from props.models.blend_weights import blend
from props.ingest.market_odds import build_market_probs
from props.maintenance.migrate import run_migrations
from props.picks.log_picks import sport_for_model

# Reuse predict_today's machinery verbatim — do NOT reinvent scoring/calibration.
from props.picks import predict_today as pt
from props.picks.predict_today import (
    load_model,
    score_and_edge,
    build_pitcher_feature_rows,
    build_batter_feature_rows,
    build_nba_player_feature_rows,
    build_derived_player_feature_rows,
    build_nba_combo_edges,
    fetch_todays_schedule_with_pitchers,
    resolve_external_to_internal_ids,
    fetch_nba_schedule_espn,
    fetch_nba_schedule,
    resolve_nba_external_to_internal_ids,
    detect_injury_expansion,
)


def _fresh_line_player_ids(sport_code: str, target_date: date) -> dict[int, int]:
    """player_id -> a representative real game_id for every player who has a fresh
    PrizePicks *standard* line for this sport (latest snapshot, 2h window).

    Mirrors the fresh-line selection in score_and_edge / build_derived_player_
    feature_rows. EXCLUDES pp_ placeholder games (home_team_id = away_team_id):
    those are PrizePicks-only stand-ins, not real scheduled games. Falls back to
    the line's game_id only when it resolves to a real game on/after today.
    """
    rows = pd.read_sql(text("""
        WITH latest AS (
            SELECT MAX(snapshot_at) AS max_snap
            FROM prop_lines
            WHERE sportsbook='prizepicks' AND sport_code=:sport
        )
        SELECT DISTINCT pl.player_id, pl.game_id
        FROM prop_lines pl
        CROSS JOIN latest
        JOIN games g ON g.game_id = pl.game_id
        WHERE pl.sportsbook='prizepicks' AND pl.sport_code=:sport
          AND pl.line_variant='standard'
          AND pl.snapshot_at >= latest.max_snap - INTERVAL '2 hours'
          -- real games only: drop pp_ placeholders where both teams are equal
          AND (g.home_team_id IS NULL OR g.home_team_id <> g.away_team_id)
    """), engine, params={"sport": sport_code})
    out: dict[int, int] = {}
    for _, r in rows.iterrows():
        out.setdefault(int(r["player_id"]), int(r["game_id"]))
    return out


# ── per-run caches ───────────────────────────────────────────────────────────
# score_universe iterates ~10 MLB models; _augment_mlb_universe was re-run per
# model, rebuilding IDENTICAL per-player features for the whole line universe each
# time (batter_features etc. are model-independent — only the final key-slice
# differs). Cache the expensive reads for one run (cleared in run()). This is the
# 28-min hog's redundancy; caching is safe because a run is a fixed target_date.
_LINE_PIDS: dict = {}
_MLB_BATTER: dict = {}
_MLB_PITCH_QUAL: dict = {}
_MLB_OPP_LINEUP: dict = {}


def _clear_caches() -> None:
    for d in (_LINE_PIDS, _MLB_BATTER, _MLB_PITCH_QUAL, _MLB_OPP_LINEUP):
        d.clear()


def _line_players_cached(sport_code: str, target_date: date) -> dict:
    if sport_code not in _LINE_PIDS:
        _LINE_PIDS[sport_code] = _fresh_line_player_ids(sport_code, target_date)
    return _LINE_PIDS[sport_code]


def _batter_feats(pid: int, target_date: date, season: str) -> dict:
    if pid not in _MLB_BATTER:
        _MLB_BATTER[pid] = pt.batter_features(pid, target_date, season)
    return dict(_MLB_BATTER[pid])  # copy — callers mutate via feats.update()


def _pitch_qual(opp_pid: int, target_date: date) -> dict:
    if opp_pid not in _MLB_PITCH_QUAL:
        _MLB_PITCH_QUAL[opp_pid] = pt.pitcher_quality_features(opp_pid, target_date)
    return _MLB_PITCH_QUAL[opp_pid]  # read-only (consumed by feats.update)


def _opp_lineup(opp_team: int, target_date: date) -> dict:
    if opp_team not in _MLB_OPP_LINEUP:
        _MLB_OPP_LINEUP[opp_team] = pt._opposing_lineup_features(opp_team, target_date)
    return _MLB_OPP_LINEUP[opp_team]


def _augment_mlb_universe(features: pd.DataFrame, games, target_date, season,
                          feature_keys, role: str) -> pd.DataFrame:
    """Add feature rows for line-carrying MLB players the likely-player builder
    missed (pinch hitters, platoon bats, spot starters). Resolves each missing
    player to whichever scheduled game his team is playing, then reuses the same
    per-player inference (batter_features / pitcher_quality_features) so the
    feature vectors match predict_today exactly.
    """
    # Map team_id -> (game_id, opposing_pitcher_id) for today's resolved games.
    team_game: dict[int, tuple] = {}
    for g in games:
        if not g.get("game_id"):
            continue
        for side in ["home", "away"]:
            tid = g.get(f"{side}_team_id")
            if tid is None:
                continue
            opp = "away" if side == "home" else "home"
            team_game[tid] = (g["game_id"], g.get(f"{opp}_pitcher_id"))

    have = set(zip(features["player_id"], features["game_id"])) if not features.empty else set()
    line_players = _line_players_cached("mlb", target_date)
    if not line_players:
        return features

    # Current team for each line-carrying player (rosters keep current_team_id fresh).
    pid_list = [int(p) for p in line_players]
    team_rows = pd.read_sql(text("""
        SELECT player_id, full_name, current_team_id
        FROM players WHERE sport_code='mlb' AND player_id = ANY(:ids)
    """), engine, params={"ids": pid_list})

    extra = []
    for _, p in team_rows.iterrows():
        pid = int(p["player_id"])
        tid = p["current_team_id"]
        # current_team_id is NULL for some players → pandas float NaN, not None.
        if pd.isna(tid) or int(tid) not in team_game:
            continue
        gid, opp_pid = team_game[int(tid)]
        if (pid, gid) in have:
            continue
        feats = _batter_feats(pid, target_date, season)
        if role == "pitcher":
            # A line-carrying pitcher: add opposing-lineup features like the
            # pitcher builder does. We don't know which side he's on, so use his
            # team's opponent lineup via the resolved game pairing.
            opp_team = None
            for g in games:
                if g.get("game_id") == gid:
                    if g.get("home_team_id") == int(tid):
                        opp_team = g.get("away_team_id")
                    elif g.get("away_team_id") == int(tid):
                        opp_team = g.get("home_team_id")
            if opp_team:
                feats.update(_opp_lineup(opp_team, target_date))
        else:
            if opp_pid is not None:
                feats.update(_pitch_qual(opp_pid, target_date))
        extra.append({
            "player_id": pid,
            "player_name": p["full_name"],
            "game_id": gid,
            **{k: feats.get(k, 0) for k in feature_keys},
        })

    if not extra:
        return features
    add = pd.DataFrame(extra)
    log.info("mlb_universe_augmented", role=role, added=len(add))
    return pd.concat([features, add], ignore_index=True) if not features.empty else add


def _augment_nba_universe(features: pd.DataFrame, nba_games, target_date, season,
                          feature_keys, injury_flags: dict) -> pd.DataFrame:
    """Add feature rows for line-carrying NBA players the likely-player builder
    missed (deep-bench guys PrizePicks still posts). Resolves each to his team's
    game and reuses _nba_player_features so vectors match predict_today."""
    team_game: dict[int, tuple] = {}   # team_id -> (game_id, opp_team_id)
    for g in nba_games:
        if not g.get("game_id"):
            continue
        for side in ["home", "away"]:
            tid = g.get(f"{side}_team_id")
            if tid is None:
                continue
            opp = "away" if side == "home" else "home"
            team_game[tid] = (g["game_id"], g.get(f"{opp}_team_id"))

    have = set(zip(features["player_id"], features["game_id"])) if not features.empty else set()
    line_players = _line_players_cached("nba", target_date)
    if not line_players:
        return features

    pid_list = [int(p) for p in line_players]
    team_rows = pd.read_sql(text("""
        SELECT player_id, full_name, current_team_id
        FROM players WHERE sport_code='nba' AND player_id = ANY(:ids)
    """), engine, params={"ids": pid_list})

    extra = []
    for _, p in team_rows.iterrows():
        pid = int(p["player_id"])
        tid = p["current_team_id"]
        # current_team_id is NULL for some players → pandas float NaN, not None.
        if pd.isna(tid) or int(tid) not in team_game:
            continue
        gid, opp_team_id = team_game[int(tid)]
        if (pid, gid) in have:
            continue
        feats = pt._nba_player_features(pid, target_date, season)
        if not feats:
            continue
        if "market_over_prob" in feature_keys:
            feats["market_over_prob"] = pt._market_over_prob_for_player(pid, gid)
        if "is_playoff" in feature_keys:
            feats["is_playoff"] = 1
        if "series_game_num" in feature_keys:
            feats["series_game_num"] = pt._series_game_num(pid, opp_team_id, target_date)
        for sf in ("series_avg_points", "series_avg_rebounds", "series_avg_assists"):
            if sf in feature_keys:
                feats[sf] = pt._series_avg_stat(pid, opp_team_id,
                                                sf.replace("series_avg_", ""), target_date)
        if "absent_teammate_avg_pts" in feature_keys:
            bump = (injury_flags or {}).get(pid, 0)
            feats["absent_teammate_avg_pts"] = (
                pt._absent_teammate_stat(pid, int(tid), "points", target_date)
                if bump > 0 else 0.0)
            feats["absent_teammate_avg_min"] = bump if bump > 0 else 0.0
            feats["n_absent_teammates"] = 1 if bump > 0 else 0
            feats["expected_usage_bump"] = round(bump / 240.0, 4) if bump > 0 else 0.0
        extra.append({
            "player_id": pid,
            "player_name": p["full_name"],
            "game_id": gid,
            "team_id": int(tid),
            **{k: feats.get(k, 0.5 if k == "market_over_prob" else 0) for k in feature_keys},
        })

    if not extra:
        return features
    add = pd.DataFrame(extra)
    log.info("nba_universe_augmented", added=len(add))
    return pd.concat([features, add], ignore_index=True) if not features.empty else add


def _finalize_probs(edges: pd.DataFrame, target_date: date,
                    market_probs: dict | None) -> pd.DataFrame:
    """Apply the SAME post-processing log_picks does before storing model_prob:
    per-direction calibration -> per-sport model/market blend. Also recompute
    edge + ev on the finalized prob so they're consistent with the stored value.

    ev = half-Kelly stake for a 2-pick PrizePicks parlay at 3x payout, sized on
    the *display-recalibrated* finalized prob — identical formula and inputs to
    picks.expected_value in log_picks: half_kelly = max(0, (3*calibrate(p)-1)/4).
    """
    from props.models.prob_calibration import calibrate

    sport_by_model = {m.name: m.sport_code for m in MODELS}
    out = edges.copy()
    out["sport_code"] = out["model_name"].map(lambda m: sport_for_model(m, sport_by_model))

    def _market_implied(row):
        if not market_probs:
            return None
        key = (str(row["player_name"]).lower().strip(), row["stat_type"],
               float(row["line_value"]))
        p = market_probs.get(key)
        if p is None:
            return None
        return p if row["direction"] == "over" else 1.0 - p

    finals = []
    for _, r in out.iterrows():
        raw = float(r["model_prob"])
        dir_prob = calibrate_dir(r["sport_code"], r["stat_type"], r["direction"], raw)
        mkt = _market_implied(r)
        model_prob = round(blend(r["sport_code"], dir_prob, mkt), 4)
        edge = round(model_prob - 0.5, 4)
        cal = calibrate(model_prob)
        ev = round(max(0.0, (3 * cal - 1) / 4), 4)
        finals.append((model_prob, edge, ev))
    out[["model_prob", "edge", "ev"]] = pd.DataFrame(finals, index=out.index)
    return out


def _upsert(scored: pd.DataFrame, target_date: date) -> int:
    """UPSERT the full scored set into scored_props (the only write path)."""
    if scored.empty:
        return 0
    n = 0
    with session_scope() as s:
        for _, r in scored.iterrows():
            gid = r.get("game_id")
            if gid is None or pd.isna(gid):
                continue
            s.execute(text("""
                INSERT INTO scored_props (
                    score_date, sport_code, game_id, player_id, stat_type,
                    line_value, direction, model_prob, edge, ev, updated_at)
                VALUES (:sd, :sc, :gid, :pid, :st, :lv, :dir, :mp, :edge, :ev, NOW())
                ON CONFLICT (game_id, player_id, stat_type, line_value)
                DO UPDATE SET
                    score_date = EXCLUDED.score_date,
                    sport_code = EXCLUDED.sport_code,
                    direction  = EXCLUDED.direction,
                    model_prob = EXCLUDED.model_prob,
                    edge       = EXCLUDED.edge,
                    ev         = EXCLUDED.ev,
                    updated_at = NOW()
            """), {
                "sd": target_date, "sc": r["sport_code"], "gid": int(gid),
                "pid": int(r["player_id"]), "st": r["stat_type"],
                "lv": float(r["line_value"]), "dir": r["direction"],
                "mp": float(r["model_prob"]), "edge": float(r["edge"]),
                "ev": float(r["ev"]),
            })
            n += 1
    return n


def _safe_augment(fn, base_features, *args, label="", **kwargs):
    """Run a universe augmenter, falling back to the un-augmented base features
    if it raises. The base (likely-player) rows are already valuable coverage, so
    an augmentation hiccup must NOT drop the whole model — it just narrows it."""
    try:
        return fn(base_features, *args, **kwargs)
    except Exception as e:
        log.warning("universe_augment_failed", label=label, error=str(e)[:200])
        return base_features


def run(target_date: date | None = None) -> pd.DataFrame:
    """Score every modeled prop on the current slate and upsert into scored_props."""
    configure_logging()
    run_migrations()                       # ensure scored_props exists
    _clear_caches()                        # fresh per-run feature caches
    today = target_date or date.today()
    season = str(today.year)
    print(db_banner())
    log.info("score_universe_start", date=today.isoformat())

    # MLB schedule resolution (shared by batter + pitcher models).
    games = resolve_external_to_internal_ids(fetch_todays_schedule_with_pitchers(today))

    # NBA schedule (lazy — only fetched once, then reused across NBA models).
    nba_games = None
    nba_injury_flags: dict = {}
    nba_pred_by_player: dict = {}          # for deriving combo-stat edges

    all_scored: list[pd.DataFrame] = []

    for entry in MODELS:
        if not entry.model_path.exists():
            log.warning("model_file_missing", name=entry.name)
            continue
        # Isolate each model: one model's API timeout / data gap must not zero out
        # the rest of the universe (same resilience contract as predict_today).
        try:
            model, meta = load_model(entry)
            fk = meta["feature_keys"]

            if entry.sport_code == "nba":
                if nba_games is None:
                    nba_games = fetch_nba_schedule_espn(today)
                    if not nba_games:
                        nba_games = resolve_nba_external_to_internal_ids(
                            fetch_nba_schedule(today))
                    nba_injury_flags = detect_injury_expansion(nba_games or [], today)
                features = build_nba_player_feature_rows(
                    nba_games, today, season, fk, injury_flags=nba_injury_flags)
                features = _safe_augment(
                    _augment_nba_universe, features,
                    nba_games, today, season, fk, nba_injury_flags, label="nba")
            elif entry.sport_code in ("wnba", "nhl", "nfl"):
                # Already full-universe (scans prop_lines, not a likely subset).
                features = build_derived_player_feature_rows(entry.sport_code, fk, today)
            elif entry.role == "pitcher":
                features = build_pitcher_feature_rows(games, today, season, fk)
                features = _safe_augment(
                    _augment_mlb_universe, features,
                    games, today, season, fk, "pitcher", label="mlb_pitcher")
            else:
                features = build_batter_feature_rows(games, today, season, fk)
                features = _safe_augment(
                    _augment_mlb_universe, features,
                    games, today, season, fk, "batter", label="mlb_batter")

            if features is None or features.empty:
                log.info("no_features", model=entry.name)
                continue
            edges = score_and_edge(model, meta, entry, features)
        except Exception as e:
            log.warning("model_failed", name=entry.name, error=str(e)[:200])
            continue

        if edges.empty:
            continue
        all_scored.append(edges)
        log.info("scored_model", model=entry.name, rows=len(edges))

        # Track NBA component predictions so combo stats (pts_rebs_asts, …) get
        # scored too — exactly as predict_today derives them.
        if entry.sport_code == "nba" and entry.stat_type in ("points", "rebounds", "assists"):
            for _, row in edges.iterrows():
                pid = int(row["player_id"])
                nba_pred_by_player.setdefault(pid, {
                    "player_name": row["player_name"],
                    "game_id": int(row["game_id"]),
                })[entry.stat_type] = float(row["predicted_mean"])

    if nba_pred_by_player:
        combo_edges = build_nba_combo_edges(nba_pred_by_player)
        if not combo_edges.empty:
            all_scored.append(combo_edges)
            log.info("scored_combo", rows=len(combo_edges))

    if not all_scored:
        log.warning("score_universe_no_props")
        return pd.DataFrame()

    combined = pd.concat(all_scored, ignore_index=True)
    # Drop any duplicate (player, stat, line, game) rows the universe augmentation
    # could have introduced — keep the first (likely-builder) row.
    combined = combined.drop_duplicates(
        subset=["player_id", "stat_type", "line_value", "game_id"], keep="first")

    market_probs = build_market_probs(today)
    scored = _finalize_probs(combined, today, market_probs)

    n = _upsert(scored, today)
    log.info("score_universe_done",
             upserted=n,
             players=int(scored["player_id"].nunique()),
             games=int(scored["game_id"].nunique()),
             sports=int(scored["sport_code"].nunique()))
    print(f"scored_props upserted: rows={n} "
          f"players={scored['player_id'].nunique()} "
          f"games={scored['game_id'].nunique()} "
          f"sports={sorted(scored['sport_code'].unique())}")
    return scored


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="Target date YYYY-MM-DD (default: today)")
    args, _ = parser.parse_known_args()
    target_date = date.fromisoformat(args.date) if args.date else None
    run(target_date)


if __name__ == "__main__":
    main()
