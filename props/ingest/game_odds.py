"""Fetch NBA game totals and spreads from ESPN's public scoreboard API.

No API key required. ESPN's scoreboard endpoint includes odds data from
ESPN Bet in competitions[0].odds when available.
"""
from datetime import date
from curl_cffi import requests as cc_requests
from sqlalchemy import text
from props.utils.db import session_scope
from props.utils.logging import log


ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"


def fetch_nba_game_context(target_date: date) -> dict[tuple, dict]:
    """Fetch ESPN scoreboard and return odds keyed by (home_abbr, away_abbr).

    Returns:
        {("OKC", "SAS"): {"total": 220.5, "home_spread": -5.5,
                          "implied_home": 113.0, "implied_away": 107.5}}
    """
    date_str = target_date.strftime("%Y%m%d")
    try:
        r = cc_requests.get(
            ESPN_SCOREBOARD,
            params={"dates": date_str, "limit": 20},
            impersonate="chrome120",
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("espn_scoreboard_fetch_failed", error=str(e))
        return {}

    result = {}
    for event in data.get("events", []):
        for comp in event.get("competitions", []):
            competitors = comp.get("competitors", [])
            home = next((c for c in competitors if c.get("homeAway") == "home"), None)
            away = next((c for c in competitors if c.get("homeAway") == "away"), None)
            if not home or not away:
                continue

            home_abbr = home.get("team", {}).get("abbreviation", "").upper()
            away_abbr = away.get("team", {}).get("abbreviation", "").upper()

            odds_list = comp.get("odds", [])
            if not odds_list:
                result[(home_abbr, away_abbr)] = {
                    "total": None, "home_spread": None,
                    "implied_home": None, "implied_away": None,
                }
                continue

            odds = odds_list[0]
            total = _parse_float(odds.get("overUnder"))
            # ESPN spread: positive means home team is giving points (home favored)
            spread = _parse_float(odds.get("spread"))

            implied_home = implied_away = None
            if total is not None and spread is not None:
                # home_advantage = spread (home gives spread points)
                implied_home = round((total + spread) / 2, 1)
                implied_away = round((total - spread) / 2, 1)

            result[(home_abbr, away_abbr)] = {
                "total": total,
                "home_spread": spread,
                "implied_home": implied_home,
                "implied_away": implied_away,
            }

    log.info("espn_game_context_fetched", games=len(result),
             date=target_date.isoformat())
    return result


def map_context_to_game_ids(
    game_context: dict[tuple, dict],
    nba_games: list,
) -> dict[int, dict]:
    """Map (home_abbr, away_abbr) context onto {internal_game_id: context_dict}.

    Also adds 'is_home_team_id' and 'is_away_team_id' keys so callers can look
    up the implied total for a specific team.
    """
    with session_scope() as session:
        rows = session.execute(text(
            "SELECT team_id, abbreviation FROM teams WHERE sport_code='nba'"
        )).all()
    abbr_by_tid = {r[0]: r[1].upper() for r in rows}

    # ESPN uses shorter abbreviations for some teams; map to ours
    ESPN_TO_OURS = {
        "SA":  "SAS",  # San Antonio
        "GS":  "GSW",  # Golden State
        "NO":  "NOP",  # New Orleans
        "NY":  "NYK",  # New York
        "PHX": "PHX",  # Phoenix (same)
    }

    result = {}
    for g in nba_games:
        gid  = g.get("game_id")
        htid = g.get("home_team_id")
        atid = g.get("away_team_id")
        if not gid or not htid or not atid:
            continue
        home_abbr = abbr_by_tid.get(htid, "")
        away_abbr = abbr_by_tid.get(atid, "")

        # Try direct match first, then ESPN short-form variants
        ctx = game_context.get((home_abbr, away_abbr))
        if ctx is None:
            # Build reverse map: our abbr → possible ESPN abbrs
            espn_home = next((k for k, v in ESPN_TO_OURS.items() if v == home_abbr), home_abbr)
            espn_away = next((k for k, v in ESPN_TO_OURS.items() if v == away_abbr), away_abbr)
            ctx = game_context.get((espn_home, espn_away))

        if ctx:
            result[gid] = {**ctx, "home_team_id": htid, "away_team_id": atid}

    return result


def _parse_float(val) -> float | None:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None
