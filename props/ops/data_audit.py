"""Data-accuracy audit — catches the kind of quietly-wrong reference data that
makes the UI lie (a league showing 6 teams when it has 30, colliding
abbreviations, placeholder/junk rows leaking into views).

It checks integrity against what each league SHOULD look like and pings Discord on
anomalies, so "truthful and accurate" is verified continuously instead of noticed
by eye. Runs in the daily pipeline and on-demand from the dashboard Ops view.

Checks:
  - team completeness   — real teams per league vs the known roster size
  - duplicate abbrevs   — two teams sharing an abbreviation (a collision bug)
  - junk games          — home==away, or a PrizePicks-placeholder team in a game
  - placeholder leakage — settled picks pointing at a placeholder team
  - stale team mapping   — players.current_team_id ≠ their most-recent game's team

Run:  python -m props.ops.data_audit          (alerts on problems)
      python -m props.ops.data_audit --quiet  (print only, no Discord)
"""
from __future__ import annotations

import argparse

from sqlalchemy import text

from props.utils.db import engine, db_banner
from props.utils.config import settings
from props.utils.logging import log, configure_logging

# Known current roster sizes — a league dropping below this means missing teams.
EXPECTED_TEAMS = {"mlb": 30, "nba": 30, "nhl": 32, "wnba": 13}
REAL_TEAM = ("COALESCE(external_id,'') <> 'PP_PLACEHOLDER' "
             "AND COALESCE(name,'') NOT ILIKE '%All-Star%'")


def run_checks() -> list[dict]:
    findings: list[dict] = []
    with engine.connect() as c:
        # ── team completeness ────────────────────────────────────────────────
        counts = {r[0]: int(r[1]) for r in c.execute(text(
            f"SELECT sport_code, COUNT(*) FROM teams WHERE {REAL_TEAM} GROUP BY 1")).all()}
        short = []
        for sport, want in EXPECTED_TEAMS.items():
            have = counts.get(sport, 0)
            if have < want:
                short.append(f"{sport}:{have}/{want}")
        if short:
            findings.append({"level": "warn", "name": "teams_incomplete",
                             "detail": "missing teams — " + ", ".join(short)
                                       + " (run props.ingest.{mlb,nhl}_teams)"})
        else:
            findings.append({"level": "ok", "name": "team_completeness",
                             "detail": ", ".join(f"{s}:{int(counts.get(s,0))}"
                                                  for s in EXPECTED_TEAMS)})

        # ── duplicate abbreviations within a league (collision bug) ──────────
        # The big multi-league / multi-division sports are exempt: cbb (~725 D1 +
        # D2/D3 opponents → Peru State + Paul Smith's both "PSC"), cfb (FBS + FCS
        # opponents), and soccer (100+ clubs across 6 leagues → Brentford/Brest
        # both "BRE", Paris FC/Parma "PAR", Torino/Toronto "TOR"). Collisions are
        # inevitable and benign there — external_id is the real key, abbr is
        # display-only. The check matters for the 30-32-team pro leagues where a
        # collision IS a bug.
        dups = c.execute(text(f"""
            SELECT sport_code, abbreviation, COUNT(*) n
            FROM teams WHERE {REAL_TEAM} AND sport_code NOT IN ('cbb', 'cfb', 'soccer')
            GROUP BY 1, 2 HAVING COUNT(*) > 1 ORDER BY 1, 2
        """)).all()
        if dups:
            findings.append({"level": "warn", "name": "duplicate_abbrev",
                             "detail": "colliding abbreviations — "
                                       + ", ".join(f"{r[0]}/{r[1]}×{r[2]}" for r in dups)})
        else:
            findings.append({"level": "ok", "name": "abbrev_unique",
                             "detail": "no abbreviation collisions"})

        # ── junk games ───────────────────────────────────────────────────────
        # `pp_` placeholder games (home==away, created when a PrizePicks prop can't
        # resolve to a real game — mostly non-league/exhibition props + doubleheaders)
        # are EXPECTED, not data bugs: ~95% are for untracked players and only 1 pick
        # has ever landed on one (the dangerous case is caught by placeholder_picks
        # below). So they're reported as an info count, NOT a daily warning. A
        # genuine bug is home==away on a REAL (non-`pp_`) game.
        junk = c.execute(text("""
            SELECT COUNT(*) FROM games g
            WHERE g.game_date >= CURRENT_DATE - 14
              AND g.home_team_id = g.away_team_id
              AND COALESCE(g.external_id, '') NOT LIKE 'pp_%'
        """)).scalar() or 0
        pp_ph = c.execute(text("""
            SELECT COUNT(*) FROM games g
            WHERE g.game_date >= CURRENT_DATE - 14
              AND g.home_team_id = g.away_team_id
              AND g.external_id LIKE 'pp_%'
        """)).scalar() or 0
        if junk:
            findings.append({"level": "warn", "name": "junk_games",
                             "detail": f"{junk} real junk game(s) in last 14d (home==away on a non-placeholder game)"})
        else:
            findings.append({"level": "ok", "name": "games_clean",
                             "detail": f"no real junk games; {pp_ph} expected pp_ placeholder(s) in last 14d (benign)"})

        # ── placeholder leakage into settled picks ───────────────────────────
        leak = c.execute(text("""
            SELECT COUNT(*) FROM picks pk
            JOIN player_games pg ON pg.player_id = pk.player_id AND pg.game_id = pk.game_id
            JOIN teams t ON t.team_id = pg.team_id
            WHERE t.external_id = 'PP_PLACEHOLDER' AND pk.leg_result IN ('win','loss','push')
        """)).scalar() or 0
        if leak:
            findings.append({"level": "warn", "name": "placeholder_picks",
                             "detail": f"{leak} settled pick(s) tied to a placeholder team"})

        # ── stale current_team_id vs most-recent game (informational) ────────
        stale = c.execute(text("""
            WITH recent AS (
                SELECT DISTINCT ON (pg.player_id) pg.player_id, pg.team_id
                FROM player_games pg JOIN games g USING (game_id)
                ORDER BY pg.player_id, g.game_date DESC, pg.player_game_id DESC
            )
            SELECT COUNT(*) FROM players p JOIN recent r USING (player_id)
            WHERE p.current_team_id IS DISTINCT FROM r.team_id
        """)).scalar() or 0
        findings.append({"level": "ok", "name": "team_mapping",
                         "detail": f"{stale} players whose current_team_id ≠ recent-game team "
                                   "(expected for recent trades; roster sync keeps current_team_id authoritative)"})

        # ── player identity: basketball box scores resolve players by FUZZY NAME,
        # which mis-attributes games to the wrong player (e.g. Jared McCain picked
        # up 45 phantom OKC games he never played). Surface candidates — a real
        # un-merge needs re-keying by authoritative ESPN athlete ids and touches
        # FK-referenced picks, so this is informational (no daily alert) until that
        # migration lands, rather than auto-merging blind.
        mismap = c.execute(text("""
            SELECT COUNT(*) FROM (
                SELECT pg.player_id FROM player_games pg JOIN games g USING (game_id)
                WHERE g.sport_code IN ('nba', 'wnba')
                GROUP BY pg.player_id HAVING COUNT(DISTINCT pg.team_id) > 2
            ) x
        """)).scalar() or 0
        combo = c.execute(text("""
            SELECT COUNT(*) FROM players
            WHERE sport_code IN ('nba', 'wnba') AND full_name LIKE '%% + %%'
        """)).scalar() or 0
        findings.append({"level": "ok", "name": "player_identity",
                         "detail": f"{mismap} NBA/WNBA players span >2 teams (fuzzy-match "
                                   f"mis-attribution candidates), {combo} combo-name junk rows "
                                   "— needs authoritative-ID re-keying (see roadmap)"})
    return findings


def _alert(findings: list[dict]):
    warns = [f for f in findings if f["level"] == "warn"]
    if not warns or not settings.discord_webhook_url:
        return
    import requests
    lines = "\n".join(f"• **{f['name']}** — {f['detail']}" for f in warns)
    payload = {"embeds": [{
        "title": "⚠️ prop-edge data audit",
        "description": f"{len(warns)} data-accuracy issue(s):\n{lines}",
        "color": 0xE8A317, "footer": {"text": "data_audit"},
    }]}
    try:
        requests.post(settings.discord_webhook_url, json=payload, timeout=10)  # type: ignore[arg-type]
    except Exception as e:
        log.warning("data_audit_alert_failed", error=str(e)[:120])


def main():
    configure_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="print only, no Discord")
    args = ap.parse_args()
    print(db_banner())
    findings = run_checks()
    for f in findings:
        print(f"  {'⚠️ ' if f['level'] == 'warn' else '✓ '}{f['name']}: {f['detail']}")
    warns = [f for f in findings if f["level"] == "warn"]
    log.info("data_audit", checks=len(findings), warnings=len(warns))
    if not args.quiet:
        _alert(findings)
    return warns


if __name__ == "__main__":
    main()
