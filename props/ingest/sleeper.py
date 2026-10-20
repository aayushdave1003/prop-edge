"""Ingest Sleeper Picks player-prop lines into prop_lines.

Sleeper's public ``/lines/available`` API is OPEN — no auth, no Cloudflare wall —
so it's the line source after PrizePicks Cloudflare-challenged its /projections
endpoint (2026-07-07; PrizePicks + Underdog are both walled now, see PROVENANCE.md).

Sleeper is a DIFFERENT book (pick'em, per-pick odds), so tracking on Sleeper lines
is a NEW track record on a new book — the models transfer unchanged (they predict
the stat; we compare the projection to whatever line the book posts). Player
mapping is by normalized full name (verified 100% for the current MLB + WNBA line
sets); game resolution reuses prizepicks.find_real_game (team + date).

Run:  python -m props.ingest.sleeper             land into prop_lines
      python -m props.ingest.sleeper --dry-run   parse + resolve + report, NO writes
"""
from __future__ import annotations

import argparse
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone

from curl_cffi import requests as cc
from sqlalchemy import text
from tenacity import retry, stop_after_attempt, wait_exponential

from props.utils.db import session_scope
from props.utils.logging import configure_logging, log

BASE = "https://api.sleeper.app"
SPORTS = ("mlb", "nba", "wnba", "nfl", "nhl", "cfb")

# (sport, Sleeper wager_type) -> our stat_type.
#
# Keyed on the PAIR, not on wager_type alone. The same string means different
# things in different sports — NFL "assists" is a defensive assisted tackle,
# NBA/NHL "assists" is the real stat — so a flat dict silently cross-wires them.
#
# The target MUST equal a `registry.MODELS` stat_type FOR THAT SPORT, or the line
# can never become a pick. Two models went dark that way: `earned_runs_allowed_v1`
# (lines landed as "earned_runs") and all three NHL models (sport not ingested).
#
# Extra aliases are harmless — an unused key costs nothing — but a MISSING key is
# a silent failure (the wager just counts as `unmodeled_stat`). So prefer listing
# plausible variants over guessing one exact string.
_BASKETBALL = {
    "points": "points", "rebounds": "rebounds", "assists": "assists",
    # Sleeper is INCONSISTENT across combo arities: the 3-way is the compact
    # "pts_reb_ast", but the 2-way combos spell it out as "X_and_Y". Verified
    # live on NBA opening night 2026-10-20. The compact 2-way forms below were
    # a guess mirrored from WNBA — which only ever posts the 3-way, so nothing
    # exercised them — and they silently discarded 394 NBA lines/day across
    # three markets we have trained models for. Kept as harmless aliases.
    "pts_reb_ast": "pts_rebs_asts", "pts_rebs_asts": "pts_rebs_asts",
    "points_and_rebounds": "pts_rebs", "pts_reb": "pts_rebs", "pts_rebs": "pts_rebs",
    "points_and_assists": "pts_asts", "pts_ast": "pts_asts", "pts_asts": "pts_asts",
    "rebounds_and_assists": "rebs_asts", "reb_ast": "rebs_asts", "rebs_asts": "rebs_asts",
}

WAGER_TO_STAT: dict[tuple[str, str], str] = {
    # ── MLB (strings verified live) ────────────────────────────────────────────
    ("mlb", "hits"): "hits",
    ("mlb", "total_bases"): "total_bases",
    ("mlb", "rbis"): "rbis",
    ("mlb", "home_runs"): "home_runs",
    ("mlb", "hits_runs_rbis"): "hits_runs_rbis",
    ("mlb", "strike_outs"): "strikeouts_pitcher",
    ("mlb", "hits_allowed"): "hits_allowed",
    ("mlb", "earned_runs"): "earned_runs_allowed",   # NOT "earned_runs" — see above
    # ── NFL (strings verified live 2026-09-07) ─────────────────────────────────
    ("nfl", "receiving_yards"): "receiving_yards",
    ("nfl", "receptions"): "receptions",
    ("nfl", "rushing_yards"): "rushing_yards",
    # ── NHL (strings verified live 2026-10-05) ─────────────────────────────────
    # Sleeper also posts points/shots/powerplay_points/blocked_shots — no model
    # for those, so they stay unmapped.
    ("nhl", "goals"): "goals",
    ("nhl", "assists"): "assists",
    ("nhl", "saves"): "saves",
    # ── CFB (strings verified live 2026-10-14; same names as NFL) ──────────────
    ("cfb", "receiving_yards"): "receiving_yards",
    ("cfb", "rushing_yards"): "rushing_yards",
    ("cfb", "passing_yards"): "passing_yards",
    ("cfb", "receptions"): "receptions",
    # ── NBA / WNBA ─────────────────────────────────────────────────────────────
    # WNBA strings verified live; NBA mirrors them (off-season at time of writing,
    # so re-check with `--dry-run` once the season opens). threes_made is NBA-only
    # — there is no wnba_threes_made model.
    ("nba", "threes_made"): "threes_made",
    **{("nba", k): v for k, v in _BASKETBALL.items()},
    **{("wnba", k): v for k, v in _BASKETBALL.items()},
}


@retry(stop=stop_after_attempt(4), wait=wait_exponential(min=1, max=8))
def _get(path: str):
    r = cc.get(f"{BASE}{path}", impersonate="chrome131", timeout=30)
    r.raise_for_status()
    return r.json()


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]", "", s.lower()).strip()


def pick_candidate(cands: list[tuple[int, int]], want_tid: int | None):
    """Choose among players sharing a normalised name, or None if undecidable.

    Normalised names are NOT unique — MLB has 8 genuine namesake pairs (two Will
    Smiths: a catcher with 444 games and a pitcher with 110) and NHL has 12. The
    only honest tiebreak is the team the line itself names; anything else is a
    guess, and a wrong attribution silently corrupts both the features used to
    price the prop and the settled record. So an undecidable line is dropped.
    """
    if len(cands) == 1:
        return cands[0]
    if want_tid is None:
        return None
    narrowed = [c for c in cands if c[1] == want_tid]
    return narrowed[0] if len(narrowed) == 1 else None


def _sleeper_name(p: dict) -> str:
    md = (p or {}).get("metadata") or {}
    return md.get("full_name") or f"{(p or {}).get('first_name', '')} {(p or {}).get('last_name', '')}".strip()


def run(dry_run: bool = False) -> None:
    configure_logging()
    started = datetime.now(timezone.utc)
    season = str(started.year)

    lines = _get("/lines/available")
    # game_id -> date, from every schedule slice a live line could belong to.
    #
    # "regular" ALONE IS NOT ENOUGH, in two ways that both fail silently (the
    # line just resolves to no date -> unresolved_game -> nothing lands):
    #
    #   * POSTSEASON. /schedule/{sport}/regular/{season} stops at the end of the
    #     regular season (mlb 2026: 3/25-9/27). Once MLB and WNBA reached the
    #     playoffs, every one of their lines became unresolvable -- measured
    #     2026-10-06, that was 292 mlb + 68 wnba lines and ZERO landable rows.
    #   * YEAR ROLLOVER. `season` is the current calendar year, but NFL/NBA/NHL
    #     seasons span it (nfl 2026: 9/09/26 - 1/10/27). Come January the year
    #     flips and the current season's schedule is no longer fetched at all.
    #
    # Unknown combinations return an empty list rather than an error, so asking
    # for slices that do not exist (nhl "post" before the playoffs, next year's
    # season) is harmless.
    sched: dict[str, str] = {}
    seasons = (season, str(int(season) - 1))
    for sp in SPORTS:
        for yr in seasons:
            for season_type in ("regular", "post"):
                try:
                    for g in _get(f"/schedule/{sp}/{season_type}/{yr}"):
                        if g.get("game_id"):
                            sched[str(g["game_id"])] = g.get("date")
                except Exception as e:
                    log.warning("sleeper_schedule_failed", sport=sp, season=yr,
                                season_type=season_type, err=str(e)[:100])
    # sleeper roster: sport -> {subject_id: full_name}
    rosters = {sp: _get(f"/players/{sp}") for sp in SPORTS}

    with session_scope() as session:
        # our players: (sport, norm_name) -> [(player_id, current_team_id), …]
        #
        # A LIST, not a single value. Normalised names are not unique: MLB has 8
        # genuine namesake pairs (two Will Smiths — a catcher with 444 games and
        # a pitcher with 110) and NHL has 12. This used to be a plain dict, so
        # the LAST row of an unordered query silently won and a line could be
        # attached to the wrong player entirely. Candidates are disambiguated
        # below by the line's own team; a line we still cannot pin is SKIPPED
        # rather than guessed at — a wrong attribution poisons the record, a
        # skipped line is just a missing row.
        our: dict[tuple[str, str], list[tuple[int, int]]] = {}
        for sp in SPORTS:
            for r in session.execute(text(
                "SELECT player_id, full_name, current_team_id FROM players WHERE sport_code=:sp"
                " ORDER BY player_id"), {"sp": sp}):
                our.setdefault((sp, _norm(r.full_name)), []).append((r.player_id, r.current_team_id))
        # Sleeper's team abbreviation -> our team_id, per sport. Used ONLY to
        # break a namesake tie (game resolution still goes through the matched
        # player's own team, so nothing else hinges on abbreviation matching).
        abbr_to_tid: dict[tuple[str, str], int] = {}
        for r in session.execute(text(
            "SELECT sport_code, abbreviation, team_id FROM teams WHERE abbreviation IS NOT NULL")):
            abbr_to_tid[(r.sport_code, r.abbreviation)] = r.team_id
        # our upcoming games keyed by (sport, team_id, date) for BOTH sides — we
        # resolve a line's game via the mapped player's OWN team, not Sleeper's
        # (ambiguous) abbreviation, so nothing hinges on abbrev matching.
        games_map: dict[tuple, int] = {}
        for r in session.execute(text("""
            SELECT sport_code, game_date, home_team_id, away_team_id, game_id
            FROM games WHERE game_date >= CURRENT_DATE - 1""")):
            for tid in (r.home_team_id, r.away_team_id):
                if tid is not None:
                    games_map[(r.sport_code, tid, str(r.game_date))] = r.game_id

        rows = []
        skip: Counter = Counter()
        seen: set = set()
        for e in lines:
            sp = e.get("sport")
            if sp not in SPORTS:
                skip["sport"] += 1
                continue
            stat = WAGER_TO_STAT.get((sp, e.get("wager_type")))
            if not stat:
                skip["unmodeled_stat"] += 1
                continue
            opts = e.get("options") or []
            over = next((o for o in opts if o.get("outcome") == "over"), None)
            under = next((o for o in opts if o.get("outcome") == "under"), None)
            if not over or over.get("outcome_value") is None:
                skip["no_line_value"] += 1
                continue
            sid = str(e.get("subject_id"))
            cands = our.get((sp, _norm(_sleeper_name(rosters[sp].get(sid, {})))), [])
            if not cands:
                skip["unmatched_player"] += 1
                continue
            chosen = pick_candidate(
                cands, abbr_to_tid.get((sp, (over.get("subject_team") or "").strip())))
            if chosen is None:
                skip["ambiguous_player"] += 1
                log.warning("sleeper_ambiguous_player", sport=sp,
                            name=_sleeper_name(rosters[sp].get(sid, {})),
                            candidates=[c[0] for c in cands],
                            sleeper_team=over.get("subject_team"))
                continue
            pid, tid = chosen
            gdate = sched.get(str(e.get("game_id")))
            gid = games_map.get((sp, tid, gdate)) if (gdate and tid) else None
            if gid is None:
                skip["unresolved_game"] += 1
                continue
            key = (pid, gid, stat)
            if key in seen:
                continue
            seen.add(key)

            def _mult(o):
                m = (o or {}).get("payout_multiplier")
                return float(m) if m is not None else None
            rows.append({"sc": sp, "pid": pid, "gid": gid, "stat": stat,
                         "line": float(over["outcome_value"]), "variant": "standard",
                         "over_payout": _mult(over), "under_payout": _mult(under),
                         "ts": started})

        print(f"Sleeper lines: {len(lines)} fetched")
        print(f"  landable prop_lines (player+game+stat resolved): {len(rows)}")
        print(f"  skipped: {dict(skip)}")
        if rows:
            by = Counter((r["sc"], r["stat"]) for r in rows)
            print("  by sport|stat:", {f"{k[0]}|{k[1]}": v for k, v in by.most_common()})
            print("  sample:", rows[0])

        if dry_run:
            print("DRY RUN — no writes.")
            return

        run_id = session.execute(text("""
            INSERT INTO ingestion_runs (source, started_at, status)
            VALUES ('sleeper_lines', :s, 'running') RETURNING run_id
        """), {"s": started}).scalar()
        if rows:
            session.execute(text("""
                INSERT INTO prop_lines (
                    sportsbook, sport_code, player_id, game_id, stat_type, line_value,
                    over_payout, under_payout, line_variant, is_pickem, snapshot_at)
                VALUES ('sleeper', :sc, :pid, :gid, :stat, :line,
                        :over_payout, :under_payout, :variant, TRUE, :ts)
            """), rows)
        session.execute(text("""
            UPDATE ingestion_runs SET completed_at=NOW(), rows_inserted=:n, status='success'
            WHERE run_id=:rid
        """), {"n": len(rows), "rid": run_id})
    log.info("sleeper_ingest_complete", inserted=len(rows), skipped=dict(skip))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="parse + resolve + report, no writes")
    args = ap.parse_args()
    run(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
