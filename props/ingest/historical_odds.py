"""Backfill historical market odds from The Odds API into market_odds table.

For each game in our DB, fetches the closing-line player prop odds from
DraftKings + FanDuel at ~2 hours before tipoff. Stores no-vig over probability
alongside raw American prices for full audit trail.

Cost: 1 request per events-snapshot + 1 per game = ~2–10 req/game-day.
With 20k monthly requests this covers all historical data + leaves room.

Usage:
    python3 -m props.ingest.historical_odds              # all sports, all history
    python3 -m props.ingest.historical_odds --sport nba  # NBA only
    python3 -m props.ingest.historical_odds --sport mlb --since 2025-04-01
"""
import argparse
import time
from datetime import date, datetime, timedelta, timezone

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

NBA_MARKETS = [
    "player_points", "player_rebounds", "player_assists",
    "player_threes", "player_blocks", "player_steals",
    "player_points_rebounds_assists",
    "player_points_rebounds", "player_points_assists",
    "player_rebounds_assists",
]

MLB_MARKETS = [
    "batter_hits", "batter_home_runs", "batter_rbis",
    "batter_strikeouts", "batter_total_bases",
    "pitcher_strikeouts", "pitcher_hits_allowed",
]

MARKET_TO_STAT = {
    "player_points":                  "points",
    "player_rebounds":                "rebounds",
    "player_assists":                 "assists",
    "player_threes":                  "threes_made",
    "player_blocks":                  "blocks",
    "player_steals":                  "steals",
    "player_points_rebounds_assists": "pts_rebs_asts",
    "player_points_rebounds":         "pts_rebs",
    "player_points_assists":          "pts_asts",
    "player_rebounds_assists":        "rebs_asts",
    "batter_hits":                    "hits",
    "batter_home_runs":               "home_runs",
    "batter_rbis":                    "rbis",
    "batter_strikeouts":              "strikeouts",
    "batter_total_bases":             "total_bases",
    "pitcher_strikeouts":             "strikeouts_pitcher",
    "pitcher_hits_allowed":           "hits_allowed",
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


def _get(url: str, params: dict, retries: int = 3) -> dict | None:
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=20)
            remaining = int(r.headers.get("x-requests-remaining", 9999))
            if remaining < 50:
                log.warning("odds_api_low_quota", remaining=remaining)
            if r.status_code == 422:
                return None   # no data for this date/event
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                log.warning("odds_api_request_failed", url=url, error=str(e))
                return None
    return None


def fetch_historical_events(sport: str, snapshot_dt: datetime) -> list[dict]:
    """Events list at a point in time."""
    url = f"{ODDS_API_BASE}/historical/sports/{SPORT_KEY[sport]}/events"
    data = _get(url, {"apiKey": _key(), "date": snapshot_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "dateFormat": "iso"})
    if not data:
        return []
    return data.get("data", [])


def fetch_historical_event_props(sport: str, event_id: str,
                                  snapshot_dt: datetime) -> list[dict]:
    """Player prop odds for one event at a point in time."""
    markets = NBA_MARKETS if sport == "nba" else MLB_MARKETS
    url = f"{ODDS_API_BASE}/historical/sports/{SPORT_KEY[sport]}/events/{event_id}/odds"
    data = _get(url, {
        "apiKey":      _key(),
        "date":        snapshot_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "regions":     "us",
        "markets":     ",".join(markets),
        "bookmakers":  ",".join(SHARP_BOOKS),
        "oddsFormat":  "american",
    })
    if not data:
        return []
    return data.get("data", {}).get("bookmakers", [])


def load_games_to_backfill(sport: str, since: date) -> pd.DataFrame:
    """Games we have player data for but no market odds yet."""
    sql = text("""
        SELECT DISTINCT g.game_id, g.game_date, g.external_id,
               g.home_team_id, g.away_team_id
        FROM games g
        JOIN player_games pg USING(game_id)
        WHERE g.sport_code = :sport
          AND g.status = 'final'
          AND g.game_date >= :since
          AND NOT EXISTS (
              SELECT 1 FROM market_odds mo
              WHERE mo.game_id = g.game_id
          )
        GROUP BY g.game_id, g.game_date, g.external_id,
                 g.home_team_id, g.away_team_id
        ORDER BY g.game_date
    """)
    df = pd.read_sql(sql, engine, params={"sport": sport, "since": since})
    df["game_date"] = pd.to_datetime(df["game_date"])
    return df


def load_player_name_map(sport: str) -> dict:
    """Lower-cased full_name → player_id lookup."""
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT player_id, LOWER(full_name) AS name FROM players WHERE sport_code=:s"
        ), {"s": sport}).fetchall()
    return {r[1]: r[0] for r in rows}


def upsert_market_odds(rows: list[dict]):
    """Insert rows, skipping duplicates."""
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


def process_game(game_row, sport: str, name_map: dict) -> int:
    """Fetch and store market odds for one game. Returns rows inserted."""
    game_id   = int(game_row["game_id"])
    game_date = game_row["game_date"]

    # Snapshot 2 hours before typical tip-off (8 PM ET = midnight UTC, so 22:00 UTC)
    snapshot_dt = datetime(
        game_date.year, game_date.month, game_date.day,
        22, 0, 0, tzinfo=timezone.utc
    )

    # Find matching event from the Odds API
    events = fetch_historical_events(sport, snapshot_dt)
    if not events:
        # Try next day for late games
        snapshot_dt = snapshot_dt + timedelta(days=1)
        events = fetch_historical_events(sport, snapshot_dt)
    if not events:
        return 0

    # Match to our game by team names (via external_id isn't reliable cross-source)
    # We'll fetch all events' props and store by matching player names
    rows = []
    for event in events:
        bookmakers = fetch_historical_event_props(sport, event["id"], snapshot_dt)
        time.sleep(0.15)  # stay under rate limit

        for bm in bookmakers:
            if bm["key"] not in SHARP_BOOKS:
                continue
            bookmaker = bm["key"]
            for market in bm.get("markets", []):
                stat = MARKET_TO_STAT.get(market["key"])
                if not stat:
                    continue

                # Group into (player, line) → {over_price, under_price}
                pairs: dict[tuple, dict] = {}
                for o in market.get("outcomes", []):
                    player_name = o.get("description", "").lower().strip()
                    line        = float(o.get("point", 0))
                    k = (player_name, line)
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
                        "snapshot_time":    snapshot_dt,
                    })

    upsert_market_odds(rows)
    return len(rows)


def run(sport: str = "nba", since: date = None):
    configure_logging()
    if since is None:
        since = date(2024, 10, 1)  # start of 2024-25 NBA season

    games = load_games_to_backfill(sport, since)
    name_map = load_player_name_map(sport)
    log.info("backfill_start", sport=sport, games=len(games), since=str(since))

    total_rows = 0
    for i, (_, game) in enumerate(games.iterrows()):
        n = process_game(game, sport, name_map)
        total_rows += n
        if i % 10 == 0:
            log.info("backfill_progress", done=i, total=len(games),
                     rows_so_far=total_rows)
        time.sleep(0.1)

    log.info("backfill_complete", sport=sport, games_processed=len(games),
             total_rows=total_rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport",  default="nba", choices=["nba", "mlb"])
    parser.add_argument("--since",  default=None,
                        help="YYYY-MM-DD. Default: 2024-10-01 for NBA, 2024-04-01 for MLB")
    args = parser.parse_args()

    since_date = None
    if args.since:
        since_date = date.fromisoformat(args.since)
    elif args.sport == "mlb":
        since_date = date(2024, 4, 1)

    run(sport=args.sport, since=since_date)
