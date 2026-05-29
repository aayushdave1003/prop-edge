"""Fetch NBA player prop lines from The Odds API for sharp-book EV comparison.

Computes the no-vig midpoint probability from DraftKings + FanDuel and returns
a lookup that predict_today uses to compute market_edge = model_prob - market_implied.

Requires ODDS_API_KEY in .env. Falls back to an empty dict (gracefully disabling
market-edge comparison) when the key is absent or the API is unavailable.

Free tier: 500 req/month. NBA season typical usage ~5–10 req/day.
"""
from datetime import date, datetime, timedelta, timezone
import requests
from props.utils.config import settings
from props.utils.logging import log


ODDS_API_BASE = "https://api.the-odds-api.com/v4"
SHARP_BOOKS   = ["draftkings", "fanduel"]

MARKET_TO_STAT = {
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
}

FETCH_MARKETS = list(MARKET_TO_STAT.keys())


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


def fetch_events(target_date: date) -> list[dict]:
    key = _get_key()
    if not key:
        log.info("odds_api_key_missing_skipping_market_odds")
        return []
    # NBA games start ~8 PM ET = midnight+ UTC; extend window by 2 days to capture them
    start = f"{target_date.strftime('%Y-%m-%d')}T00:00:00Z"
    end   = f"{(target_date + timedelta(days=2)).strftime('%Y-%m-%d')}T23:59:59Z"
    try:
        r = requests.get(
            f"{ODDS_API_BASE}/sports/basketball_nba/events",
            params={"apiKey": key, "dateFormat": "iso",
                    "commenceTimeFrom": start, "commenceTimeTo": end},
            timeout=15,
        )
        r.raise_for_status()
        remaining = r.headers.get("x-requests-remaining", "?")
        events = r.json()
        log.info("odds_api_events", count=len(events), requests_remaining=remaining)
        return events
    except Exception as e:
        log.warning("odds_api_events_failed", error=str(e))
        return []


def fetch_event_props(event_id: str) -> dict:
    key = _get_key()
    if not key:
        return {}
    try:
        r = requests.get(
            f"{ODDS_API_BASE}/sports/basketball_nba/events/{event_id}/odds",
            params={
                "apiKey":      key,
                "regions":     "us",
                "markets":     ",".join(FETCH_MARKETS),
                "bookmakers":  ",".join(SHARP_BOOKS),
                "oddsFormat":  "american",
            },
            timeout=20,
        )
        r.raise_for_status()
        remaining = r.headers.get("x-requests-remaining", "?")
        log.info("odds_api_props", event_id=event_id, requests_remaining=remaining)
        return r.json()
    except Exception as e:
        log.warning("odds_api_props_failed", event_id=event_id, error=str(e))
        return {}


def build_market_probs(target_date: date) -> dict[tuple, float]:
    """Return {(player_name_lower, stat_type, line_value): no_vig_over_prob}.

    Averages across all available sharp books. Returns empty dict when no
    API key is configured or the fetch fails.
    """
    events = fetch_events(target_date)
    if not events:
        return {}

    # Accumulate over/under prices per (player, stat, line) across books
    raw: dict[tuple, dict[str, list]] = {}

    for event in events:
        data = fetch_event_props(event["id"])
        if not data:
            continue
        for bm in data.get("bookmakers", []):
            if bm["key"] not in SHARP_BOOKS:
                continue
            for market in bm.get("markets", []):
                stat = MARKET_TO_STAT.get(market["key"])
                if not stat:
                    continue
                # Group outcomes by (player, line)
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

    if not raw:
        return {}

    result = {}
    for k, prices in raw.items():
        avg_over  = sum(prices["over"])  / len(prices["over"])
        avg_under = sum(prices["under"]) / len(prices["under"])
        result[k] = round(_no_vig_prob(avg_over, avg_under), 4)

    log.info("market_probs_built", props=len(result))
    return result
