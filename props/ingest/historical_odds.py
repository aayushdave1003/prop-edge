"""Backfill historical market odds from The Odds API into market_odds table.

EFFICIENT design:
  - Groups games by date → 1 events-list request per date (not per game)
  - Matches each game to its correct Odds API event by team name
  - Fetches only the matched event's props (not all events on that day)
  - Fetches only 3 core markets to minimize request cost

Request cost: (1 events + 3 markets × 1 per matched event) per date
  ~4 requests/date × 200 NBA dates = ~800 requests for full season
  (vs 19,000+ with the original buggy approach)

Usage:
    python3 -m props.ingest.historical_odds              # NBA since 2024-10-01
    python3 -m props.ingest.historical_odds --sport mlb --since 2025-04-01
    python3 -m props.ingest.historical_odds --dry-run    # count games only
"""
import argparse
import time
from datetime import date, datetime, timedelta, timezone
from collections import defaultdict

import pandas as pd
import requests
from sqlalchemy import text

from props.utils.config import settings
from props.utils.db import engine, session_scope
from props.utils.logging import log, configure_logging


ODDS_API_BASE = "https://api.the-odds-api.com/v4"
SHARP_BOOKS   = ["draftkings", "fanduel"]

SPORT_KEY = {
    "nba": "basketball_nba",
    "mlb": "baseball_mlb",
}

NBA_MARKETS = ["player_points", "player_rebounds", "player_assists",
               "player_threes", "player_points_rebounds_assists"]
MLB_MARKETS = ["batter_hits", "batter_home_runs", "batter_total_bases",
               "pitcher_strikeouts"]

MARKET_TO_STAT = {
    "player_points":                  "points",
    "player_rebounds":                "rebounds",
    "player_assists":                 "assists",
    "player_threes":                  "threes_made",
    "player_points_rebounds_assists": "pts_rebs_asts",
    "batter_hits":                    "hits",
    "batter_home_runs":               "home_runs",
    "batter_total_bases":             "total_bases",
    "pitcher_strikeouts":             "strikeouts_pitcher",
}


def _key() -> str:
    return getattr(settings, "odds_api_key", "") or ""


def _american_to_implied(price: float) -> float:
    if price < 0:
        return -price / (-price + 100)
    return 100 / (price + 100)


def _no_vig_prob(over_price: float, under_price: float) -> float:
    p_over  = _american_to_implied(over_price)
    p_under = _american_to_implied(under_price)
    return round(p_over / (p_over + p_under), 4)


# Drives the --max-requests budget. IMPORTANT: historical endpoints bill ~20×
# per HTTP call (per market × the historical multiplier), so the budget must be
# measured in BILLED requests (the x-requests-remaining delta), not HTTP calls —
# otherwise a "1000" budget could burn ~20k real credits. We track both.
_requests_made = 0                 # HTTP calls made (diagnostic)
_last_remaining: int | None = None # most recent x-requests-remaining
_start_remaining: int | None = None  # remaining before our first billed call


def _billed_used() -> int:
    """Credits this process has actually consumed (header delta), falling back to
    the HTTP-call count if the quota header was never seen."""
    if _start_remaining is not None and _last_remaining is not None:
        return max(0, _start_remaining - _last_remaining)
    return _requests_made


def _get(url: str, params: dict, retries: int = 3) -> dict | None:
    global _requests_made, _last_remaining, _start_remaining
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=20)
            _requests_made += 1
            remaining = int(r.headers.get("x-requests-remaining", 9999))
            # Capture the pre-call balance once, so billed = start - current.
            if _start_remaining is None:
                _start_remaining = remaining + 1
            _last_remaining = remaining
            if remaining < 20:
                log.warning("odds_api_quota_critical", remaining=remaining)
                return None
            if r.status_code == 422:
                return None
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                log.warning("odds_api_request_failed", url=url, error=str(e))
                return None
    return None


def _team_nickname(full_name: str) -> str:
    """Extract last word of team name for fuzzy matching."""
    return full_name.strip().split()[-1].lower()


def load_games_by_date(sport: str, since: date) -> dict[date, list]:
    """Group DB games by game_date for efficient date-level API calls."""
    sql = text("""
        SELECT g.game_id, g.game_date, g.external_id,
               g.home_team_id, g.away_team_id,
               ht.city || ' ' || ht.name AS home_name,
               at.city || ' ' || at.name AS away_name,
               ht.name AS home_nickname, at.name AS away_nickname
        FROM games g
        JOIN teams ht ON ht.team_id = g.home_team_id
        JOIN teams at ON at.team_id = g.away_team_id
        WHERE g.sport_code = :sport
          AND g.status = 'final'
          AND g.game_date >= :since
          AND NOT EXISTS (
              SELECT 1 FROM market_odds mo WHERE mo.game_id = g.game_id
          )
        ORDER BY g.game_date, g.game_id
    """)
    df = pd.read_sql(sql, engine, params={"sport": sport, "since": since})
    df["game_date"] = pd.to_datetime(df["game_date"]).dt.date

    by_date = defaultdict(list)
    for _, row in df.iterrows():
        by_date[row["game_date"]].append(row.to_dict())
    return dict(by_date)


def load_player_name_map(sport: str) -> dict:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT player_id, LOWER(full_name) AS name FROM players WHERE sport_code=:s"
        ), {"s": sport}).fetchall()
    return {r[1]: r[0] for r in rows}


def match_event_to_game(events: list[dict], game: dict) -> dict | None:
    """Find the Odds API event matching this DB game by team nickname."""
    home_nick = game["home_nickname"].strip().lower()
    away_nick = game["away_nickname"].strip().lower()

    for event in events:
        api_home = event.get("home_team", "").lower()
        api_away = event.get("away_team", "").lower()
        # Match if both nicknames appear in the API team names
        if home_nick in api_home and away_nick in api_away:
            return event
        if away_nick in api_home and home_nick in api_away:
            return event
    return None


def fetch_events_for_date(sport: str, game_date: date) -> list[dict]:
    """Fetch events snapshot at 10 PM UTC on game_date (before most tip-offs)."""
    snapshot = f"{game_date.strftime('%Y-%m-%d')}T22:00:00Z"
    url  = f"{ODDS_API_BASE}/historical/sports/{SPORT_KEY[sport]}/events"
    data = _get(url, {"apiKey": _key(), "date": snapshot, "dateFormat": "iso"})
    if not data:
        # Try next day midnight for late games
        next_day = (game_date + timedelta(days=1)).strftime("%Y-%m-%d")
        data = _get(url, {"apiKey": _key(), "date": f"{next_day}T01:00:00Z",
                          "dateFormat": "iso"})
    return (data or {}).get("data", [])


def fetch_event_props(sport: str, event_id: str, game_date: date) -> list[dict]:
    """Fetch props for ONE matched event. Cost: 1 req per market."""
    markets = NBA_MARKETS if sport == "nba" else MLB_MARKETS
    snapshot = f"{game_date.strftime('%Y-%m-%d')}T22:00:00Z"
    url  = f"{ODDS_API_BASE}/historical/sports/{SPORT_KEY[sport]}/events/{event_id}/odds"
    data = _get(url, {
        "apiKey":     _key(),
        "date":       snapshot,
        "regions":    "us",
        "markets":    ",".join(markets),
        "bookmakers": ",".join(SHARP_BOOKS),
        "oddsFormat": "american",
    })
    if not data:
        return []
    return (data.get("data") or {}).get("bookmakers", [])


def parse_bookmaker_props(bookmakers: list, game_id: int,
                          name_map: dict) -> list[dict]:
    rows = []
    for bm in bookmakers:
        if bm["key"] not in SHARP_BOOKS:
            continue
        bookmaker = bm["key"]
        for market in bm.get("markets", []):
            stat = MARKET_TO_STAT.get(market["key"])
            if not stat:
                continue
            pairs: dict[tuple, dict] = {}
            for o in market.get("outcomes", []):
                player = o.get("description", "").lower().strip()
                line   = float(o.get("point", 0))
                k = (player, line)
                pairs.setdefault(k, {})
                pairs[k][o["name"].lower()] = int(o["price"])
            for (player_name, line), prices in pairs.items():
                if "over" not in prices or "under" not in prices:
                    continue
                player_id = name_map.get(player_name)
                if player_id is None:
                    continue
                rows.append({
                    "game_id":          game_id,
                    "player_id":        player_id,
                    "stat_type":        stat,
                    "line_value":       line,
                    "over_price":       prices["over"],
                    "under_price":      prices["under"],
                    "market_over_prob": _no_vig_prob(prices["over"], prices["under"]),
                    "bookmaker":        bookmaker,
                    "snapshot_time":    datetime.utcnow(),
                })
    return rows


def upsert_market_odds(rows: list[dict]):
    if not rows:
        return
    sql = text("""
        INSERT INTO market_odds
            (game_id, player_id, stat_type, line_value,
             over_price, under_price, market_over_prob, bookmaker, snapshot_time)
        VALUES
            (:game_id, :player_id, :stat_type, :line_value,
             :over_price, :under_price, :market_over_prob, :bookmaker, :snapshot_time)
        ON CONFLICT (game_id, player_id, stat_type, line_value, bookmaker)
        DO UPDATE SET
            over_price       = EXCLUDED.over_price,
            under_price      = EXCLUDED.under_price,
            market_over_prob = EXCLUDED.market_over_prob,
            snapshot_time    = EXCLUDED.snapshot_time
    """)
    with session_scope() as session:
        session.execute(sql, rows)


def run(sport: str = "nba", since: date = None, dry_run: bool = False,
        max_requests: int | None = None, recent_first: bool = False):
    """Backfill / refresh market_odds for games not yet covered.

    ``max_requests`` caps billed API calls this run (a budget) so a scheduled
    *refresh* can top up recent games without draining the monthly quota; once
    the budget is hit the run stops cleanly and the next run resumes where it
    left off (load_games_by_date already skips games already in market_odds).
    ``recent_first`` processes the newest dates first — what a refresh wants, so
    the backtest gets current data even if the budget runs out mid-backfill."""
    configure_logging()
    if since is None:
        since = date(2024, 10, 1)

    n_markets = len(NBA_MARKETS if sport == "nba" else MLB_MARKETS)
    games_by_date = load_games_by_date(sport, since)
    name_map      = load_player_name_map(sport)

    total_dates = len(games_by_date)
    total_games = sum(len(v) for v in games_by_date.values())
    est_requests = total_dates * 1 + total_games * n_markets

    log.info("backfill_plan", sport=sport, dates=total_dates, games=total_games,
             est_requests=est_requests, max_requests=max_requests,
             recent_first=recent_first)

    if dry_run:
        budget = f", budget {max_requests}" if max_requests else ""
        print(f"\nDry run: {total_dates} dates, {total_games} games, "
              f"~{est_requests} requests needed{budget}")
        return

    total_rows = 0
    matched    = 0
    unmatched  = 0
    stopped    = False

    def _over_budget() -> bool:
        # Stop once we've actually CONSUMED the budget in billed credits (the
        # header delta), since one historical call bills many credits.
        return max_requests is not None and _billed_used() >= max_requests

    items = sorted(games_by_date.items(), reverse=recent_first)
    for i, (game_date, day_games) in enumerate(items):
        if _over_budget():
            stopped = True
            break

        events = fetch_events_for_date(sport, game_date)
        if not events:
            unmatched += len(day_games)
            continue

        for game in day_games:
            if _over_budget():
                stopped = True
                break
            event = match_event_to_game(events, game)
            if not event:
                log.debug("no_event_match", game_id=game["game_id"],
                          home=game["home_nickname"], away=game["away_nickname"])
                unmatched += 1
                continue

            bookmakers = fetch_event_props(sport, event["id"], game_date)
            time.sleep(0.2)

            rows = parse_bookmaker_props(bookmakers, int(game["game_id"]), name_map)
            upsert_market_odds(rows)
            total_rows += len(rows)
            matched += 1

        if stopped:
            break
        if i % 10 == 0:
            log.info("backfill_progress", dates_done=i, total_dates=total_dates,
                     matched=matched, unmatched=unmatched, rows=total_rows,
                     requests=_requests_made)
        time.sleep(0.1)

    log.info("backfill_complete", sport=sport, dates=total_dates,
             matched=matched, unmatched=unmatched, total_rows=total_rows,
             http_calls=_requests_made, billed_credits=_billed_used(),
             remaining=_last_remaining, stopped_on_budget=stopped)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport",    default="nba", choices=["nba", "mlb"])
    parser.add_argument("--since",    default=None)
    parser.add_argument("--dry-run",  action="store_true")
    parser.add_argument("--max-requests", type=int, default=None,
                        help="cap billed API requests this run (budget for a refresh)")
    parser.add_argument("--recent-first", action="store_true",
                        help="process newest dates first (for keeping the backtest current)")
    args = parser.parse_args()

    since_date = None
    if args.since:
        since_date = date.fromisoformat(args.since)
    elif args.sport == "mlb":
        since_date = date(2025, 4, 1)

    run(sport=args.sport, since=since_date, dry_run=args.dry_run,
        max_requests=args.max_requests, recent_first=args.recent_first)
