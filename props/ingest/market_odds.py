"""Fetch live player prop lines from The Odds API for sharp-book EV comparison.

Computes the no-vig midpoint probability from DraftKings + FanDuel and returns a
lookup keyed by (player, stat, line) that predict_today uses to compute
market_edge and the model/market blend. Covers NBA + MLB (the sports with deep,
sharply-priced prop markets).

Requires ODDS_API_KEY in .env. Falls back to an empty dict (gracefully disabling
market comparison) when the key is absent or the API is unavailable.
"""
from datetime import date, timedelta
import requests
from props.utils.config import settings
from props.utils.logging import log


ODDS_API_BASE = "https://api.the-odds-api.com/v4"
SHARP_BOOKS   = ["draftkings", "fanduel"]

# The Odds API sport keys for the leagues we price.
SPORT_KEYS = {"nba": "basketball_nba", "mlb": "baseball_mlb"}

MARKET_TO_STAT = {
    # NBA
    "player_points":                  "points",
    "player_rebounds":                "rebounds",
    "player_assists":                 "assists",
    "player_threes":                  "threes_made",
    "player_blocks":                  "blocks",
    "player_steals":                  "steals",
    "player_turnovers":               "turnovers",
    "player_points_rebounds_assists": "pts_rebs_asts",
    "player_points_rebounds":         "pts_rebs",
    "player_points_assists":          "pts_asts",
    "player_rebounds_assists":        "rebs_asts",
    # MLB
    "batter_hits":                    "hits",
    "batter_home_runs":               "home_runs",
    "batter_total_bases":             "total_bases",
    "pitcher_strikeouts":             "strikeouts_pitcher",
}

# Core markets to fetch per sport (each market bills, so keep tight).
FETCH_MARKETS_BY_SPORT = {
    "nba": ["player_points", "player_rebounds", "player_assists",
            "player_threes", "player_points_rebounds_assists"],
    "mlb": ["batter_hits", "batter_home_runs", "batter_total_bases",
            "pitcher_strikeouts"],
}
# Back-compat alias (NBA) for any external import.
FETCH_MARKETS = FETCH_MARKETS_BY_SPORT["nba"]


def _american_to_implied(price: float) -> float:
    if price < 0:
        return -price / (-price + 100)
    return 100 / (price + 100)


def _no_vig_prob(over_price: float, under_price: float) -> float:
    """Remove the bookmaker's juice; return the true implied over probability."""
    p_over  = _american_to_implied(over_price)
    p_under = _american_to_implied(under_price)
    return p_over / (p_over + p_under)


def _get_key() -> str:
    return getattr(settings, "odds_api_key", "") or ""


def fetch_events(target_date: date, sport: str = "nba") -> list[dict]:
    key = _get_key()
    if not key:
        log.info("odds_api_key_missing_skipping_market_odds")
        return []
    sport_key = SPORT_KEYS.get(sport)
    if not sport_key:
        return []
    # Games start in the evening local = next-day UTC; extend the window 2 days.
    start = f"{target_date.strftime('%Y-%m-%d')}T00:00:00Z"
    end   = f"{(target_date + timedelta(days=2)).strftime('%Y-%m-%d')}T23:59:59Z"
    try:
        r = requests.get(
            f"{ODDS_API_BASE}/sports/{sport_key}/events",
            params={"apiKey": key, "dateFormat": "iso",
                    "commenceTimeFrom": start, "commenceTimeTo": end},
            timeout=15,
        )
        r.raise_for_status()
        remaining = r.headers.get("x-requests-remaining", "?")
        events = r.json()
        log.info("odds_api_events", sport=sport, count=len(events),
                 requests_remaining=remaining)
        return events
    except Exception as e:
        log.warning("odds_api_events_failed", sport=sport, error=str(e))
        return []


def fetch_event_props(event_id: str, sport: str = "nba") -> dict:
    key = _get_key()
    if not key:
        return {}
    sport_key = SPORT_KEYS.get(sport)
    if not sport_key:
        return {}
    try:
        r = requests.get(
            f"{ODDS_API_BASE}/sports/{sport_key}/events/{event_id}/odds",
            params={
                "apiKey":      key,
                "regions":     "us",
                "markets":     ",".join(FETCH_MARKETS_BY_SPORT[sport]),
                "bookmakers":  ",".join(SHARP_BOOKS),
                "oddsFormat":  "american",
            },
            timeout=20,
        )
        r.raise_for_status()
        remaining = r.headers.get("x-requests-remaining", "?")
        log.info("odds_api_props", sport=sport, event_id=event_id,
                 requests_remaining=remaining)
        return r.json()
    except Exception as e:
        log.warning("odds_api_props_failed", sport=sport, event_id=event_id, error=str(e))
        return {}


def _build_for_sport(target_date: date, sport: str, out: dict) -> None:
    """Accumulate no-vig over-probs for one sport into ``out`` (keyed by
    (player_name_lower, stat_type, line_value))."""
    events = fetch_events(target_date, sport)
    if not events:
        return
    raw: dict[tuple, dict[str, list]] = {}
    for event in events:
        data = fetch_event_props(event["id"], sport)
        if not data:
            continue
        for bm in data.get("bookmakers", []):
            if bm["key"] not in SHARP_BOOKS:
                continue
            for market in bm.get("markets", []):
                stat = MARKET_TO_STAT.get(market["key"])
                if not stat:
                    continue
                pairs: dict[tuple, dict] = {}
                for o in market.get("outcomes", []):
                    player = o.get("description", "").lower().strip()
                    line   = float(o.get("point", 0))
                    k      = (player, stat, line)
                    pairs.setdefault(k, {})
                    pairs[k][o["name"].lower()] = float(o["price"])
                for k, prices in pairs.items():
                    if "over" not in prices or "under" not in prices:
                        continue
                    raw.setdefault(k, {"over": [], "under": []})
                    raw[k]["over"].append(prices["over"])
                    raw[k]["under"].append(prices["under"])
    for k, prices in raw.items():
        avg_over  = sum(prices["over"])  / len(prices["over"])
        avg_under = sum(prices["under"]) / len(prices["under"])
        out[k] = round(_no_vig_prob(avg_over, avg_under), 4)


def build_market_probs(target_date: date, sports=("nba", "mlb")) -> dict[tuple, float]:
    """Return {(player_name_lower, stat_type, line_value): no_vig_over_prob}
    across the given sports (NBA + MLB by default). Averages across sharp books.
    Empty dict when no key is configured or nothing is fetched. Stat names don't
    collide across sports, so one merged dict is safe."""
    result: dict[tuple, float] = {}
    for sport in sports:
        try:
            _build_for_sport(target_date, sport, result)
        except Exception as e:
            log.warning("market_probs_sport_failed", sport=sport, error=str(e)[:120])
    log.info("market_probs_built", props=len(result), sports=list(sports))
    return result
