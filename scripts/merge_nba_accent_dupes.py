"""One-time migration: merge NBA players forked by an accent into one row.

WHY
---
`nba_boxscores.resolve_player` matched on pg_trgm `similarity(full_name, :name)
> 0.8`, which compares raw bytes. An accent is several trigrams of difference:

    similarity('Pacôme Dadiet', 'Pacome Dadiet') = 0.647
    similarity('Egor Dëmin',    'Egor Demin')    = 0.571
    similarity('Dennis Schröder','Dennis Schroder') = 0.684

ESPN writes unaccented names and stats.nba.com writes accented ones, so every
accented player silently forked into a second `espn_` row. By the 2026 preseason
that was 18 players -- including Jokić, Dončić, Porziņģis and Vučević -- with
their recent games stranded on the fork, away from the multi-season history the
rolling features are built from. With the NBA opener on 2026-10-20 those are
exactly the players carrying the most prop lines.

The companion fix in `resolve_player` (accent-folded match first, using the same
normalisation `sleeper._norm` applies to a line feed's name) stops new forks.
This script repairs the rows already split.

WHAT IT DOES
------------
For each pair of NBA rows whose names are identical once accent-folded, where
one is an `espn_` row and the other is not, it re-points every child row
(player_games, prop_lines, picks, predictions, market_odds) from the `espn_`
fork onto the canonical row, then deletes the emptied fork.

The canonical row is the NON-espn_ one: it is created by the stats.nba.com path
and carries the long history plus the accented spelling the rest of the data
uses. Verified beforehand that no game appears on BOTH rows, so no
(player_id, game_id) unique violation is possible; the script re-checks per pair
and skips any colliding row rather than losing it.

Safety: one transaction, all-or-nothing. DRY RUN BY DEFAULT -- pass --apply.
Idempotent: a second run finds nothing.

Run:  python scripts/merge_nba_accent_dupes.py            # report only
      python scripts/merge_nba_accent_dupes.py --apply    # commit
"""
import argparse
import re
import unicodedata
from collections import defaultdict

from sqlalchemy import text

from props.utils.db import session_scope, db_banner
from props.utils.logging import log, configure_logging

CHILD_TABLES = ("player_games", "prop_lines", "picks", "predictions", "market_odds")


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]", "", s.lower()).strip()


def find_pairs(session):
    """Return [(canonical_row, fork_row)] for accent-forked NBA players."""
    rows = list(session.execute(text(
        "SELECT player_id, external_id, full_name FROM players WHERE sport_code='nba'")))
    by_fold = defaultdict(list)
    for r in rows:
        by_fold[_fold(r.full_name)].append(r)

    pairs = []
    for folded, group in by_fold.items():
        if len(group) < 2 or not folded:
            continue
        forks = [r for r in group if str(r.external_id).startswith("espn_")]
        canon = [r for r in group if not str(r.external_id).startswith("espn_")]
        if len(canon) != 1 or not forks:
            # Not the accent pattern (e.g. two genuinely different players whose
            # names fold together). Leave it alone — merging would destroy data.
            continue
        for f in forks:
            pairs.append((canon[0], f))
    return pairs


def main() -> int:
    configure_logging()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="commit (default: dry run)")
    args = ap.parse_args()
    print(db_banner())

    with session_scope() as session:
        pairs = find_pairs(session)
        print(f"accent-forked NBA pairs: {len(pairs)}\n")
        moved_total = 0
        for canon, fork in pairs:
            counts = {}
            for tbl in CHILD_TABLES:
                counts[tbl] = session.execute(
                    text(f"SELECT count(*) FROM {tbl} WHERE player_id=:p"),  # noqa: S608 - fixed allowlist
                    {"p": fork.player_id}).scalar()
            moved_total += sum(counts.values())
            detail = " ".join(f"{k.split('_')[0]}={v}" for k, v in counts.items() if v)
            print(f"  {fork.full_name!r} (pid={fork.player_id}) -> {canon.full_name!r} "
                  f"(pid={canon.player_id})   {detail or 'no child rows'}")

            if not args.apply:
                continue

            for tbl in CHILD_TABLES:
                if tbl == "player_games":
                    # UNIQUE (player_id, game_id): never create a violation. Move
                    # only games the canonical row lacks; an overlapping row is
                    # redundant with one already on the canonical player.
                    session.execute(text("""
                        UPDATE player_games a SET player_id = :c
                        WHERE a.player_id = :f
                          AND NOT EXISTS (SELECT 1 FROM player_games b
                                          WHERE b.player_id = :c AND b.game_id = a.game_id)
                    """), {"c": canon.player_id, "f": fork.player_id})
                    session.execute(text("DELETE FROM player_games WHERE player_id = :f"),
                                    {"f": fork.player_id})
                else:
                    session.execute(
                        text(f"UPDATE {tbl} SET player_id = :c WHERE player_id = :f"),  # noqa: S608
                        {"c": canon.player_id, "f": fork.player_id})
            session.execute(text("DELETE FROM players WHERE player_id = :f"),
                            {"f": fork.player_id})

        if not args.apply:
            print(f"\nDRY RUN — nothing written ({moved_total} child rows would move). "
                  f"Re-run with --apply to commit.")
            return 0
        log.info("nba_accent_dupes_merged", pairs=len(pairs), child_rows=moved_total)
        print(f"\nAPPLIED: {len(pairs)} forks merged, {moved_total} child rows re-pointed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
