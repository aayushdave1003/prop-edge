"""Roster sync — set players.current_team_id from each league's CURRENT official
roster, so an active player's team is authoritative (a trade moves him) instead of
drifting to wherever we last saw him play.

Identity matches the way each sport keys players, so the join is right:
  MLB  — statsapi team roster   → person.id  == our external_id   (exact id)
  NHL  — api-web /roster/{tri}  → player id   == our external_id   (exact id)
  NBA  — ESPN team roster       → matched by NORMALIZED NAME (our nba external_ids
         are PrizePicks ids / espn_<name> slugs, not ESPN athlete ids)
  WNBA — ESPN team roster       → matched by NORMALIZED NAME (same reason)

We only UPDATE current_team_id for players found on a current roster — we do NOT
clear anyone (name matching is fuzzy, and the dashboard already hides inactive
players via a recency filter, so a departed player's stale team is invisible).
PrizePicks placeholder players (pp_*) are never touched. Idempotent; ~110 light
requests; safe to run daily.

Run:  python -m props.ingest.rosters                 # all leagues
      python -m props.ingest.rosters --only nba mlb   # subset
"""
from __future__ import annotations

import argparse
import re
import unicodedata

import requests
from sqlalchemy import text

from props.utils.db import engine, session_scope
from props.utils.logging import log, configure_logging

H = {"User-Agent": "prop-edge/1.0"}
ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/{lg}/teams"
ESPN_ROSTER = "https://site.api.espn.com/apis/site/v2/sports/basketball/{lg}/teams/{tid}/roster"
NBA_ESPN_ALIAS = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS",
                  "UTAH": "UTA", "WSH": "WAS"}


def _norm(name: str) -> str:
    """Normalize a name for matching: drop accents, punctuation, Jr/Sr/III."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[.'’`]", "", s.lower())
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _our_teams(sport: str, key: str) -> dict[str, int]:
    with engine.connect() as c:
        return {str(r[0]): int(r[1]) for r in c.execute(text(f"""
            SELECT {key}, team_id FROM teams
            WHERE sport_code = :s AND COALESCE(external_id,'') <> 'PP_PLACEHOLDER'
              AND COALESCE(name,'') NOT ILIKE '%All-Star%' AND {key} IS NOT NULL
        """), {"s": sport})}


# Each fetcher returns (mapping, kind, fetched, total): mapping is key→team_id
# where kind says how to resolve key to a player ('id' = external_id, 'name').
def _mlb():
    teams = {k: v for k, v in _our_teams("mlb", "external_id").items() if k.isdigit()}
    out, ok = {}, 0
    for ext_team, tid in teams.items():
        try:
            r = requests.get(f"https://statsapi.mlb.com/api/v1/teams/{ext_team}/roster"
                             "?rosterType=40Man", timeout=15).json()
            for p in r.get("roster", []):
                out[str(p["person"]["id"])] = tid
            ok += 1
        except Exception as e:
            log.warning("mlb_roster_failed", team=ext_team, error=str(e)[:80])
    return out, "id", ok, len(teams)


def _nhl():
    teams = _our_teams("nhl", "abbreviation")
    out, ok = {}, 0
    for abbr, tid in teams.items():
        try:
            r = requests.get(f"https://api-web.nhle.com/v1/roster/{abbr}/current",
                             headers=H, timeout=15).json()
            for grp in ("forwards", "defensemen", "goalies"):
                for p in r.get(grp, []):
                    out[str(p["id"])] = tid
            ok += 1
        except Exception as e:
            log.warning("nhl_roster_failed", team=abbr, error=str(e)[:80])
    return out, "id", ok, len(teams)


def _espn(sport: str, lg: str, alias: dict[str, str]):
    by_abbr = _our_teams(sport, "abbreviation")
    by_name = {k.lower(): v for k, v in _our_teams(sport, "name").items()}
    espn_teams = requests.get(ESPN.format(lg=lg), timeout=15).json() \
        ["sports"][0]["leagues"][0]["teams"]
    out, ok = {}, 0
    for et in espn_teams:
        t = et["team"]
        abbr, short = t.get("abbreviation", ""), (t.get("shortDisplayName") or "").lower()
        tid = by_abbr.get(abbr) or by_abbr.get(alias.get(abbr, "")) or by_name.get(short)
        if not tid:
            log.warning("espn_team_unmatched", sport=sport, abbr=abbr, short=short)
            continue
        try:
            rr = requests.get(ESPN_ROSTER.format(lg=lg, tid=t["id"]), timeout=15).json()
            for a in rr.get("athletes", []):
                nm = a.get("fullName") or a.get("displayName")
                if nm:
                    out[_norm(nm)] = tid
            ok += 1
        except Exception as e:
            log.warning("espn_roster_failed", sport=sport, team=abbr, error=str(e)[:80])
    return out, "name", ok, len(espn_teams)


FETCHERS = {
    "mlb": _mlb,
    "nhl": _nhl,
    "nba": lambda: _espn("nba", "nba", NBA_ESPN_ALIAS),
    "wnba": lambda: _espn("wnba", "wnba", {}),
}


def _resolve(sport: str, mapping: dict[str, int], kind: str) -> dict[int, int]:
    """key→team_id  ==>  player_id→team_id (skips pp_* placeholders)."""
    with engine.connect() as c:
        rows = c.execute(text(
            "SELECT player_id, external_id, full_name FROM players "
            "WHERE sport_code = :s AND COALESCE(external_id,'') NOT LIKE 'pp_%'"),
            {"s": sport}).all()
    out: dict[int, int] = {}
    if kind == "id":
        for pid, ext, _ in rows:
            tid = mapping.get(str(ext))
            if tid:
                out[pid] = tid
    else:  # name
        by_norm: dict[str, int] = {}
        for pid, _, nm in rows:
            by_norm.setdefault(_norm(nm), pid)
        for nm_key, tid in mapping.items():
            pid = by_norm.get(nm_key)
            if pid:
                out[pid] = tid
    return out


def sync_sport(sport: str) -> dict:
    mapping, kind, fetched, total = FETCHERS[sport]()
    pid_team = _resolve(sport, mapping, kind)
    changed = 0
    with session_scope() as s:
        for pid, tid in pid_team.items():
            res = s.execute(text("""
                UPDATE players SET current_team_id = :tid
                WHERE player_id = :pid AND current_team_id IS DISTINCT FROM :tid
            """), {"tid": tid, "pid": pid})
            changed += res.rowcount or 0
    out = {"sport": sport, "coverage": f"{fetched}/{total}",
           "roster_players": len(mapping), "matched_players": len(pid_team),
           "team_changed": changed, "match_kind": kind}
    log.info("roster_synced", **out)
    return out


def main():
    configure_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", choices=list(FETCHERS))
    args = ap.parse_args()
    sports = args.only or list(FETCHERS)
    print(f"=== Roster sync ({', '.join(sports)}) ===")
    for sport in sports:
        try:
            r = sync_sport(sport)
            print(f"  {sport:5} teams {r['coverage']}  roster={r['roster_players']:4} "
                  f"matched={r['matched_players']:4} ({r['match_kind']})  "
                  f"team_updated={r['team_changed']}")
        except Exception as e:
            print(f"  {sport:5} FAILED — {str(e)[:120]}")
            log.warning("roster_sync_sport_failed", sport=sport, error=str(e)[:160])


if __name__ == "__main__":
    main()
