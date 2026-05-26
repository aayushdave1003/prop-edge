"""One-time migration: merge NBA placeholder players into real-ID players.

For each NBA player with external_id like 'pp_%', try to find a matching
real-NBA-API player by exact full_name match. If found:
  1. UPDATE all prop_lines.player_id from placeholder -> real
  2. DELETE the placeholder player row
If not found (e.g. combo props like "X + Y"), leave the placeholder alone.

Idempotent: re-running does nothing once merge is complete.
"""
from sqlalchemy import text
from props.utils.db import session_scope, engine
from props.utils.logging import log, configure_logging
import pandas as pd


def main():
    configure_logging()

    placeholders = pd.read_sql(text("""
        SELECT player_id, full_name, external_id
        FROM players
        WHERE sport_code='nba' AND external_id LIKE 'pp_%'
        ORDER BY full_name
    """), engine)
    log.info("placeholders_found", n=len(placeholders))

    if placeholders.empty:
        log.info("nothing_to_merge")
        return

    real_players = pd.read_sql(text("""
        SELECT player_id, full_name
        FROM players
        WHERE sport_code='nba' AND external_id NOT LIKE 'pp_%'
    """), engine)
    real_name_to_id = dict(zip(real_players["full_name"], real_players["player_id"]))
    log.info("real_players", n=len(real_players))

    merges = []
    unmatched = []
    for _, ph in placeholders.iterrows():
        real_id = real_name_to_id.get(ph["full_name"])
        if real_id is not None and real_id != ph["player_id"]:
            merges.append({
                "placeholder_id": int(ph["player_id"]),
                "real_id": int(real_id),
                "name": ph["full_name"],
            })
        else:
            unmatched.append(ph["full_name"])

    log.info("merge_plan", to_merge=len(merges), unmatched=len(unmatched))
    if unmatched[:10]:
        log.info("unmatched_sample", names=unmatched[:10])

    with session_scope() as session:
        for m in merges:
            # Repoint prop_lines
            updated_lines = session.execute(text("""
                UPDATE prop_lines
                SET player_id = :real_id
                WHERE player_id = :ph_id
            """), {"real_id": m["real_id"], "ph_id": m["placeholder_id"]}).rowcount

            # Repoint picks (in case any old NBA picks point to placeholders)
            updated_picks = session.execute(text("""
                UPDATE picks
                SET player_id = :real_id
                WHERE player_id = :ph_id
            """), {"real_id": m["real_id"], "ph_id": m["placeholder_id"]}).rowcount

            # Delete the placeholder
            session.execute(text("""
                DELETE FROM players WHERE player_id = :ph_id
            """), {"ph_id": m["placeholder_id"]})

            log.info("merged", name=m["name"],
                     placeholder_id=m["placeholder_id"], real_id=m["real_id"],
                     lines_moved=updated_lines, picks_moved=updated_picks)

    log.info("merge_complete", merged=len(merges))


if __name__ == "__main__":
    main()
