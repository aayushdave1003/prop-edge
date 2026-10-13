"""One-time migration: store NHL players under their FULL names.

WHY
---
`nhl_boxscores` took its name from the boxscore endpoint, which only ever
returns an abbreviated form ("B. Montour"). Every line feed uses the full name
("Brandon Montour"), and `sleeper._norm` cannot bridge the two -- so NHL box
scores were divorced from NHL lines. Measured 2026-10-13: 1,659 of 1,738 NHL
player rows were abbreviated and only 34 of 286 live Sleeper NHL names matched,
so the sport produced no picks at all despite three registered models.

The companion fix in `nhl_boxscores` (roster-sourced names + an ON CONFLICT that
never downgrades a full name to an abbreviation) stops this recurring. This
script repairs the rows that are already wrong.

WHAT IT DOES
------------
  1. RENAME: for each abbreviated row whose external_id appears on a current NHL
     roster, set full_name to "First Last".
  2. DELETE: a rename can collide with an older PrizePicks-era stub row
     ('pp_%' external_id) that already holds the full name. Every such stub is
     verified EMPTY across all five tables that reference players -- player_games,
     prop_lines, picks, predictions, market_odds -- and only then removed. A stub
     holding any data aborts the whole run rather than guessing at a merge.

Safety: one transaction, all-or-nothing. DRY RUN BY DEFAULT -- pass --apply to
commit. Idempotent: a second run finds nothing to do.

Run:  python scripts/repair_nhl_names.py            # report only
      python scripts/repair_nhl_names.py --apply    # commit
"""
import argparse
import re

import requests
from sqlalchemy import text

from props.utils.db import session_scope, db_banner
from props.utils.logging import log, configure_logging

ROSTER_URL = "https://api-web.nhle.com/v1/roster/{abbr}/current"
HEADERS = {"User-Agent": "prop-edge/1.0"}
ABBREV = re.compile(r"^[A-Z]\.\s")

# Every table with a FK to players.player_id. A stub must be empty in ALL of
# them before it can be deleted.
CHILD_TABLES = ("player_games", "prop_lines", "picks", "predictions", "market_odds")


def fetch_roster_names(session) -> dict[str, str]:
    """external_id -> "First Last" across every current NHL club roster."""
    abbrs = [r[0] for r in session.execute(text(
        "SELECT abbreviation FROM teams WHERE sport_code='nhl' AND abbreviation IS NOT NULL"))]
    names: dict[str, str] = {}
    for abbr in abbrs:
        try:
            r = requests.get(ROSTER_URL.format(abbr=abbr), headers=HEADERS, timeout=15)
            if not r.ok:
                continue          # national sides (Olympics / 4 Nations) 404 — expected
            for group in ("forwards", "defensemen", "goalies"):
                for p in r.json().get(group, []):
                    first = (p.get("firstName") or {}).get("default", "")
                    last = (p.get("lastName") or {}).get("default", "")
                    if first and last and p.get("id") is not None:
                        names[str(p["id"])] = f"{first} {last}"
        except Exception as e:
            log.warning("roster_fetch_failed", team=abbr, err=str(e)[:80])
    return names


def _norm(s: str) -> str:
    """Mirror of sleeper._norm — the matching the repair is meant to fix."""
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]", "", s.lower()).strip()


def build_plan(session, roster: dict[str, str]):
    """Return (renames, deletes). Raises if a colliding stub holds data."""
    rows = list(session.execute(text(
        "SELECT player_id, external_id, full_name FROM players WHERE sport_code='nhl'")))
    by_norm: dict[str, list] = {}
    for r in rows:
        by_norm.setdefault(_norm(r.full_name), []).append(r)

    renames, deletes, seen_del = [], [], set()
    for r in rows:
        if not ABBREV.match(r.full_name or ""):
            continue
        new = roster.get(str(r.external_id))
        if not new or _norm(new) == _norm(r.full_name):
            continue
        for other in by_norm.get(_norm(new), []):
            if other.player_id == r.player_id or other.player_id in seen_del:
                continue
            counts = {t: session.execute(
                text(f"SELECT count(*) FROM {t} WHERE player_id=:p"),  # noqa: S608 - fixed allowlist
                {"p": other.player_id}).scalar() for t in CHILD_TABLES}
            if any(counts.values()):
                raise SystemExit(
                    f"ABORT: player_id={other.player_id} ({other.full_name!r}) would be "
                    f"displaced by the rename of {r.player_id} but holds data {counts}. "
                    f"That needs a reviewed merge, not a delete — nothing was written.")
            seen_del.add(other.player_id)
            deletes.append(other)
        renames.append((r, new))
    return renames, deletes


def main() -> int:
    configure_logging()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="commit (default: dry run)")
    args = ap.parse_args()
    print(db_banner())

    with session_scope() as session:
        roster = fetch_roster_names(session)
        print(f"roster names fetched: {len(roster)}")
        renames, deletes = build_plan(session, roster)
        print(f"\nPLAN: {len(renames)} renames, {len(deletes)} empty pp_ stubs to delete")
        for r, new in renames[:5]:
            print(f"   rename  {r.full_name!r} -> {new!r}")
        if len(renames) > 5:
            print(f"   … and {len(renames) - 5} more")
        for d in deletes[:5]:
            print(f"   delete  player_id={d.player_id} {d.full_name!r} (external_id={d.external_id})")
        if len(deletes) > 5:
            print(f"   … and {len(deletes) - 5} more")

        if not args.apply:
            print("\nDRY RUN — nothing written. Re-run with --apply to commit.")
            return 0

        # Deletes first: they free the names the renames are about to take.
        for d in deletes:
            session.execute(text("DELETE FROM players WHERE player_id=:p"), {"p": d.player_id})
        for r, new in renames:
            session.execute(text("UPDATE players SET full_name=:n WHERE player_id=:p"),
                            {"n": new, "p": r.player_id})
        log.info("nhl_names_repaired", renamed=len(renames), deleted=len(deletes))
        print(f"\nAPPLIED: {len(renames)} renamed, {len(deletes)} stubs deleted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
