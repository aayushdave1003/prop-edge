"""Snapshot Kalshi's prop-ladder quotes — a sharp, EXECUTABLE market reference.

Why this exists (2026-11-24): every edge claim we've ever tested was "our
probability vs a payout we invert through unknown vig". Kalshi quotes are
different in kind — a 0.59 ask IS a tradable market probability. Storing
game-day ladders gives us, for free:
  1. per-prop calibration of our models against an executable price,
  2. a Sleeper-vs-Kalshi divergence tracker (props.models.kalshi_compare) —
     the honest version of the "soft book" hypothesis the arb lane chased,
  3. genuinely pre-game market snapshots (market_odds' 22:00-UTC snapshot is
     post-game for day games and leaked AUC 0.77 into backtests).

Honest-lane rules: measurement ONLY. No picks come from this table, no
Discord, no EV claims. It is a reference feed, so series without models
(nfl passing_yards) belong here — this is not WAGER_TO_STAT.

⚠️ LEAK GUARD for every consumer: rows carry snapshot_at. A snapshot taken
after kickoff is in-play/post-game data. Always join games.game_datetime and
require snapshot_at < game_datetime (kalshi_compare does; copy it).

Mechanics, verified live 2026-11-24:
  - Public REST, no auth. Quotes live in the markets list's *_dollars fields
    (yes_bid_dollars/yes_ask_dollars) — one paginated call per series, no
    per-market order-book fetches needed.
  - Books are EMPTY until ~game day, then quote with 1-2c spreads on liquid
    strikes; far-future games may carry junk-wide MM quotes (0.03/0.08).
    Everything quoted is stored raw; consumers filter by spread.
  - Ticker anatomy: SERIES-26NOV24DALDEN-DALPWASHINGTON25-25 →
    event_date 2026-11-24, matchup DALDEN, threshold 25 ("25+" = over 24.5).
    Player display name comes from yes_sub_title ("P.J. Washington: 25+").

Run:  python -m props.ingest.kalshi_lines            # snapshot all series
      python -m props.ingest.kalshi_lines --dry-run  # fetch + report, no writes
"""
import argparse
from collections import Counter
from datetime import datetime, timezone

import requests
from sqlalchemy import text

from props.utils.db import session_scope
from props.utils.logging import log, configure_logging
from props.maintenance.migrate import run_migrations

BASE = "https://api.elections.kalshi.com/trade-api/v2"

# Series → (our sport_code, our stat_type). Verified live 2026-11-24: each of
# these had open markets with *_dollars quotes. Probed but 0 open that day
# (exist, maybe seasonal/never-offered — re-probe before adding): KXNBATHREES,
# KXNHLSAVES, KXNHLPOINTS, KXNHLASSISTS, KXNHLSOG. Per-series counts are
# logged every run, so a series going dark is visible, not silent.
SERIES = {
    "KXNFLPASSYDS": ("nfl", "passing_yards"),
    "KXNFLRECYDS":  ("nfl", "receiving_yards"),
    "KXNFLRSHYDS":  ("nfl", "rushing_yards"),
    "KXNFLREC":     ("nfl", "receptions"),
    "KXNBAPTS":     ("nba", "points"),
    "KXNBAAST":     ("nba", "assists"),
    "KXNBAREB":     ("nba", "rebounds"),
    "KXNBAPRA":     ("nba", "pts_rebs_asts"),
    "KXNBA3PT":     ("nba", "threes_made"),
    "KXNHLGOAL":    ("nhl", "goals"),
}


def threshold_to_sleeper_line(threshold: float) -> float:
    """Kalshi "X+" (resolves YES at >= X) is the over side of a Sleeper X-0.5
    line for integer stats. kalshi_compare joins on this equivalence."""
    return threshold - 0.5


def _f(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_market(m: dict, series: str):
    """Pure parse of one markets-list item → (row dict, None) or (None, reason).
    Unquoted markets (no bid AND no ask) are skipped: a ladder strike nobody
    will trade is not a market price."""
    sport, stat = SERIES[series]
    ticker = m.get("ticker") or ""
    parts = ticker.split("-")
    if len(parts) != 4:
        return None, "ticker_shape"
    _, ev, _, thr_s = parts
    try:
        threshold = float(thr_s)
    except ValueError:
        return None, "threshold"
    try:
        event_date = datetime.strptime(ev[:7], "%y%b%d").date()
    except ValueError:
        return None, "event_date"
    sub = m.get("yes_sub_title") or ""
    player = sub.rsplit(":", 1)[0].strip() if ":" in sub else ""
    if not player:
        return None, "player_name"
    yes_bid, yes_ask = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
    if not yes_bid and not yes_ask:
        return None, "unquoted"
    return {
        "market_ticker": ticker, "series_ticker": series,
        "sport_code": sport, "stat_type": stat,
        "event_date": event_date, "matchup": ev[7:], "player_name": player,
        "threshold": threshold, "yes_bid": yes_bid, "yes_ask": yes_ask,
        "volume": _f(m.get("volume_fp")) or _f(m.get("volume")),
    }, None


def fetch_series(series: str) -> list[dict]:
    out: list[dict] = []
    cursor = ""
    while True:
        params: dict[str, str | int] = {"series_ticker": series,
                                        "status": "open", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        r = requests.get(f"{BASE}/markets", params=params, timeout=30)
        r.raise_for_status()
        d = r.json()
        batch = d.get("markets", [])
        out.extend(batch)
        cursor = d.get("cursor")
        if not cursor or not batch:
            return out


_INSERT = text("""
    INSERT INTO kalshi_lines (snapshot_at, market_ticker, series_ticker,
        sport_code, stat_type, event_date, matchup, player_name, threshold,
        yes_bid, yes_ask, volume)
    VALUES (:snapshot_at, :market_ticker, :series_ticker, :sport_code,
        :stat_type, :event_date, :matchup, :player_name, :threshold,
        :yes_bid, :yes_ask, :volume)
    ON CONFLICT DO NOTHING
""")


def run(dry_run: bool = False) -> None:
    configure_logging()
    if not dry_run:
        run_migrations()  # ensures kalshi_lines exists (migration 0019)
    snapshot_at = datetime.now(timezone.utc)
    all_rows: list[dict] = []
    skips: Counter = Counter()
    for series in SERIES:
        markets = fetch_series(series)
        rows = []
        for m in markets:
            row, reason = parse_market(m, series)
            if row:
                rows.append({**row, "snapshot_at": snapshot_at})
            else:
                skips[reason] += 1
        # per-series count every run: a dark series is visible, not silent
        log.info("kalshi_series", series=series, open_markets=len(markets),
                 quoted=len(rows))
        all_rows.extend(rows)
    if dry_run:
        print(f"DRY RUN — {len(all_rows)} quoted rows, skips={dict(skips)}")
        for r in all_rows[:8]:
            print(f"  {r['sport_code']}|{r['stat_type']:15s} {r['player_name']:24s}"
                  f" {r['threshold']:g}+  {r['yes_bid']}/{r['yes_ask']}")
        return
    if all_rows:
        with session_scope() as s:
            s.execute(_INSERT, all_rows)
    log.info("kalshi_lines_stored", rows=len(all_rows), skips=dict(skips),
             snapshot_at=snapshot_at.isoformat())


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch + parse + report, no writes")
    args = ap.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
