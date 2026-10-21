"""One-time cleanup: remove PrizePicks placeholder game rows.

WHY
---
`prizepicks.resolve_game_id` creates a stand-in `games` row (external_id
`pp_<id>`) when it cannot match a scraped projection to a real scheduled game.
Those rows describe no actual contest: both sides are "PrizePicks Placeholder",
they are created minutes after a real game's start time, and they stay
`status='scheduled'` forever because no box score can ever arrive.

410 had accumulated between 2026-05-25 and 2026-10-20. They are not merely
untidy -- they inflate every per-day game count (which is how a 3-game NBA
opening night read as 6), and they sit permanently in the unsettleable pile.

The source that produced them is gone: PrizePicks has been Cloudflare-walled
since 2026-07 and the pipeline moved to Sleeper via the LineFeed seam. The local
Mac cron that kept invoking it (scrape_lines.sh, 4x/day) was removed at the same
time as this cleanup; it had been failing 100% of runs.

SAFETY
------
Deletes only rows whose external_id matches `pp\\_%`. Every pick that ever landed
on one is already settled as `void` (verified: 46 of 46), and no `player_games`
reference them, so nothing gradeable is lost. Child rows are removed first --
prop_lines and predictions hold a FK to games. Anything NOT void aborts the run
rather than destroying a real result.

Run:  python scripts/purge_placeholder_games.py            # report only
      python scripts/purge_placeholder_games.py --apply    # commit
"""
import argparse

from sqlalchemy import text

from props.utils.db import session_scope, db_banner
from props.utils.logging import log, configure_logging

PLACEHOLDER = r"external_id LIKE 'pp\_%'"


def main() -> int:
    configure_logging()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="commit (default: dry run)")
    args = ap.parse_args()
    print(db_banner())

    with session_scope() as s:
        games = s.execute(text(f"SELECT count(*) FROM games WHERE {PLACEHOLDER}")).scalar()
        if not games:
            print("no placeholder games — nothing to do")
            return 0

        # A placeholder must never be holding a real, graded result.
        bad = s.execute(text(f"""
            SELECT count(*) FROM picks pk JOIN games g ON g.game_id = pk.game_id
            WHERE g.{PLACEHOLDER} AND (pk.leg_result IS DISTINCT FROM 'void')
        """)).scalar()
        if bad:
            raise SystemExit(
                f"ABORT: {bad} pick(s) on placeholder games are not void. A placeholder "
                f"cannot produce a real result, so this needs investigating before any "
                f"delete — nothing was written.")

        counts = {}
        for tbl in ("prop_lines", "predictions", "picks"):
            counts[tbl] = s.execute(text(f"""
                SELECT count(*) FROM {tbl} x JOIN games g ON g.game_id = x.game_id
                WHERE g.{PLACEHOLDER}
            """)).scalar()  # noqa: S608 - fixed table allowlist
        orphan_pg = s.execute(text(f"""
            SELECT count(*) FROM player_games x JOIN games g ON g.game_id = x.game_id
            WHERE g.{PLACEHOLDER}""")).scalar()

        print(f"\nplaceholder games: {games}")
        for t, n in counts.items():
            print(f"  {t:14} {n}")
        print(f"  player_games   {orphan_pg}   (must be 0 — a placeholder has no box score)")
        if orphan_pg:
            raise SystemExit("ABORT: placeholder games hold box-score rows — unexpected.")

        # A pick whose placeholder later resolved was re-pointed to the real game
        # by settle_picks.resolve_placeholder_game_ids() -- but its PREDICTION was
        # left behind on the placeholder. Those predictions are still referenced by
        # real, graded picks, so deleting them would sever the pick-to-prediction
        # link that the calibration and dispersion analyses rely on. Finish the
        # resolution instead: move each stranded prediction onto the same real game
        # its pick already points at.
        strand_sql = r"""
            SELECT pr.prediction_id, pk.game_id AS real_game_id
            FROM picks pk
            JOIN predictions pr ON pr.prediction_id = pk.prediction_id
            JOIN games pg ON pg.game_id = pr.game_id
            JOIN games kg ON kg.game_id = pk.game_id
            WHERE pg.external_id LIKE 'pp\_%' AND kg.external_id NOT LIKE 'pp\_%'
        """
        stranded = s.execute(text(strand_sql)).all()
        print(f"  stranded predictions to re-point onto their real game: {len(stranded)}")

        if not args.apply:
            print("\nDRY RUN — nothing written. Re-run with --apply to commit.")
            return 0

        for pred_id, real_gid in stranded:
            s.execute(text("UPDATE predictions SET game_id=:g WHERE prediction_id=:p"),
                      {"g": real_gid, "p": pred_id})
        if stranded:
            log.info("stranded_predictions_repointed", n=len(stranded))

        # Children first: both hold a FK to games.
        for tbl in ("picks", "predictions", "prop_lines"):
            s.execute(text(f"""
                DELETE FROM {tbl} x USING games g
                WHERE g.game_id = x.game_id AND g.{PLACEHOLDER}
            """))  # noqa: S608 - fixed table allowlist
        s.execute(text(f"DELETE FROM games WHERE {PLACEHOLDER}"))
        log.info("placeholder_games_purged", games=games, **counts)
        print(f"\nAPPLIED: {games} placeholder games removed, "
              f"{len(stranded)} predictions re-pointed onto their real game, "
              f"remaining child rows cleared.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
