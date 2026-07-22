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
SPORTS = ("mlb", "wnba", "nfl")

# Sleeper wager_type -> our stat_type. Only stats we actually model; anything
# else (doubles, singles, stolen_bases, threes_made w/o a WNBA model, …) is skipped.
WAGER_TO_STAT = {
    # MLB
    "hits": "hits", "total_bases": "total_bases", "rbis": "rbis",
    "home_runs": "home_runs", "strike_outs": "strikeouts_pitcher",
    "hits_allowed": "hits_allowed", "earned_runs": "earned_runs",
    "hits_runs_rbis": "hits_runs_rbis", "outs": "outs",
    # WNBA
    "points": "points", "rebounds": "rebounds", "assists": "assists",
    "pts_reb_ast": "pts_rebs_asts",
    # NFL — VERIFY exact wager_type strings via `--dry-run` in-season (Sleeper may
    # use rush_yd / rec_yd / pass_yd). Aliases map to one internal stat; unused keys
    # are harmless, unmapped wagers are safely skipped (unmodeled_stat).
    "rushing_yards": "rushing_yards", "rush_yards": "rushing_yards", "rush_yd": "rushing_yards",
    "receiving_yards": "receiving_yards", "rec_yards": "receiving_yards", "rec_yd": "receiving_yards",
    "passing_yards": "passing_yards", "pass_yards": "passing_yards", "pass_yd": "passing_yards",
    "receptions": "receptions", "reception": "receptions",
}


@retry(stop=stop_after_attempt(4), wait=wait_exponential(min=1, max=8))
def _get(path: str):
    r = cc.get(f"{BASE}{path}", impersonate="chrome131", timeout=30)
    r.raise_for_status()
    return r.json()


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]", "", s.lower()).strip()


def _sleeper_name(p: dict) -> str:
    md = (p or {}).get("metadata") or {}
    return md.get("full_name") or f"{(p or {}).get('first_name', '')} {(p or {}).get('last_name', '')}".strip()


def run(dry_run: bool = False) -> None:
    configure_logging()
    started = datetime.now(timezone.utc)
    season = str(started.year)

    lines = _get("/lines/available")
    # game_id -> date (from each sport's season schedule)
    sched: dict[str, str] = {}
    for sp in SPORTS:
        try:
            for g in _get(f"/schedule/{sp}/regular/{season}"):
                if g.get("game_id"):
                    sched[str(g["game_id"])] = g.get("date")
        except Exception as e:
            log.warning("sleeper_schedule_failed", sport=sp, err=str(e)[:100])
    # sleeper roster: sport -> {subject_id: full_name}
    rosters = {sp: _get(f"/players/{sp}") for sp in SPORTS}

    with session_scope() as session:
        # our players: (sport, norm_name) -> (player_id, current_team_id)
        our: dict[tuple[str, str], tuple[int, int]] = {}
        for sp in SPORTS:
            for r in session.execute(text(
                "SELECT player_id, full_name, current_team_id FROM players WHERE sport_code=:sp"), {"sp": sp}):
                our[(sp, _norm(r.full_name))] = (r.player_id, r.current_team_id)
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
            stat = WAGER_TO_STAT.get(e.get("wager_type"))
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
            match = our.get((sp, _norm(_sleeper_name(rosters[sp].get(sid, {})))))
            if not match:
                skip["unmatched_player"] += 1
                continue
            pid, tid = match
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
