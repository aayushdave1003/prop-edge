"""One-time migration: fold PrizePicks-era `pp_` stub rows into the real player.

WHY
---
A player can exist twice: once under the league's own id (MLBAM, NHL) and once
under a `pp_<id>` stub created by the old PrizePicks ingest. Both normalise to
the same name, and `sleeper.py` keys its player map on that normalised name — so
a duplicate makes the lookup ambiguous and a line can attach to the row with no
history, which prices the prop off nothing.

WHAT IT DOES
------------
For a normalised name held by exactly one real-id row and one or more `pp_`
stubs, re-point every child row (player_games, prop_lines, picks, predictions,
market_odds) onto the real row and delete the stub.

WHAT IT REFUSES TO DO
---------------------
Merge two REAL-id rows. A league id is authoritative: two different MLBAM ids
are two different people. MLB has 8 such genuine namesake pairs (two Will
Smiths, a catcher and a pitcher; two Luis Garcias) and NHL has 12. Merging them
would destroy real history. Those are handled at lookup time instead, by
`sleeper.pick_candidate`, which splits namesakes on the team the line names and
drops the line when it cannot tell them apart.

Safety: one transaction, all-or-nothing. DRY RUN BY DEFAULT -- pass --apply.
Idempotent.

Run:  python scripts/cleanup_pp_stubs.py            # report only
      python scripts/cleanup_pp_stubs.py --apply    # commit
"""
import argparse
import re
import unicodedata
from collections import defaultdict

from sqlalchemy import text

from props.utils.db import session_scope, db_banner
from props.utils.logging import log, configure_logging

SPORTS = ("mlb", "nhl", "nba", "wnba", "nfl")
CHILD_TABLES = ("player_games", "prop_lines", "picks", "predictions", "market_odds")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]", "", s.lower()).strip()


def find_pairs(session, sport: str):
    """[(real_row, stub_row)] for names held by one real row + N pp_ stubs."""
    rows = list(session.execute(text(
        "SELECT player_id, external_id, full_name FROM players WHERE sport_code=:sp"),
        {"sp": sport}))
    by_norm = defaultdict(list)
    for r in rows:
        by_norm[_norm(r.full_name)].append(r)

    pairs = []
    for name, group in by_norm.items():
        if len(group) < 2 or not name:
            continue
        stubs = [r for r in group if str(r.external_id).startswith("pp_")]
        real = [r for r in group if not str(r.external_id).startswith("pp_")]
        if len(real) != 1 or not stubs:
            # Two real ids = two real people. Never merge; see module docstring.
            continue
        for stub in stubs:
            pairs.append((real[0], stub))
    return pairs


def main() -> int:
    configure_logging()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="commit (default: dry run)")
    args = ap.parse_args()
    print(db_banner())

    total_pairs = total_moved = 0
    with session_scope() as session:
        for sport in SPORTS:
            pairs = find_pairs(session, sport)
            if not pairs:
                continue
            print(f"\n{sport.upper()}: {len(pairs)} stub(s) to fold")
            for real, stub in pairs:
                counts = {}
                for tbl in CHILD_TABLES:
                    counts[tbl] = session.execute(
                        text(f"SELECT count(*) FROM {tbl} WHERE player_id=:p"),  # noqa: S608 - fixed allowlist
                        {"p": stub.player_id}).scalar()
                moved = sum(counts.values())
                total_pairs += 1
                total_moved += moved
                detail = " ".join(f"{k.split('_')[0]}={v}" for k, v in counts.items() if v)
                print(f"   {stub.full_name!r} stub pid={stub.player_id} -> real pid={real.player_id}"
                      f" ({real.external_id})   {detail or 'empty'}")
                if not args.apply:
                    continue
                for tbl in CHILD_TABLES:
                    if tbl == "player_games":
                        # UNIQUE (player_id, game_id) — move only what the real
                        # row lacks; an overlapping row duplicates one it has.
                        session.execute(text("""
                            UPDATE player_games a SET player_id = :r
                            WHERE a.player_id = :s
                              AND NOT EXISTS (SELECT 1 FROM player_games b
                                              WHERE b.player_id = :r AND b.game_id = a.game_id)
                        """), {"r": real.player_id, "s": stub.player_id})
                        session.execute(text("DELETE FROM player_games WHERE player_id = :s"),
                                        {"s": stub.player_id})
                    else:
                        session.execute(
                            text(f"UPDATE {tbl} SET player_id = :r WHERE player_id = :s"),  # noqa: S608
                            {"r": real.player_id, "s": stub.player_id})
                session.execute(text("DELETE FROM players WHERE player_id = :s"),
                                {"s": stub.player_id})

        if not args.apply:
            print(f"\nDRY RUN — nothing written ({total_pairs} stubs, {total_moved} child rows). "
                  f"Re-run with --apply to commit.")
            return 0
        log.info("pp_stubs_folded", stubs=total_pairs, child_rows=total_moved)
        print(f"\nAPPLIED: {total_pairs} stubs folded, {total_moved} child rows re-pointed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
