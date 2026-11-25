"""Sleeper-vs-Kalshi divergence report — measurement only, no picks.

For every prop where both venues priced the SAME player/stat/threshold on the
same slate, compares Sleeper's implied over-probability (1/over_payout,
vig-INCLUSIVE) with Kalshi's order-book mid (≈ vig-free; Kalshi's cost lives
in the spread + fees, not the mid).

Reading the output honestly:
  - The MEAN diff should sit a few points above zero purely because Sleeper's
    implied prob carries its margin. That is vig, not signal.
  - Signal, if any, is in the SPREAD of diffs and persistent per-player
    outliers: a Sleeper price repeatedly far from an executable Kalshi mid in
    the direction that settles against Sleeper. No such claim is made until a
    forward sample exists; this tool only accumulates the comparison.

Leak guard (inherited from the market_odds lesson): only Kalshi rows with
snapshot_at < games.game_datetime are used — pre-game quotes only. Quotes
wider than MAX_SPREAD are excluded (junk MM placeholders on far-out games).

Run:  python -m props.models.kalshi_compare                # today's slate
      python -m props.models.kalshi_compare --date 2026-11-24
"""
import argparse
from collections import defaultdict
from datetime import datetime, date
from zoneinfo import ZoneInfo

from sqlalchemy import text

from props.utils.db import engine

MAX_SPREAD = 0.06   # same two-sided-and-tight bar as the parlay study

_SQL = text("""
    WITH k AS (
        SELECT DISTINCT ON (market_ticker)
               market_ticker, sport_code, stat_type, event_date, player_name,
               threshold::float AS threshold, yes_bid::float AS yes_bid,
               yes_ask::float AS yes_ask, snapshot_at
        FROM kalshi_lines
        WHERE event_date = :d
          AND yes_bid IS NOT NULL AND yes_ask IS NOT NULL
          AND (yes_ask - yes_bid) <= :max_spread
        ORDER BY market_ticker, snapshot_at DESC
    ),
    s AS (
        SELECT DISTINCT ON (pl.player_id, pl.stat_type)
               pl.player_id, p.full_name, g.sport_code, pl.stat_type,
               pl.line_value::float AS line, pl.over_payout::float AS over_payout,
               g.game_datetime
        FROM prop_lines pl
        JOIN players p USING (player_id)
        JOIN games g ON g.game_id = pl.game_id
        WHERE pl.sportsbook = 'sleeper' AND pl.line_variant = 'standard'
          AND g.game_date = :d AND pl.over_payout IS NOT NULL
        ORDER BY pl.player_id, pl.stat_type, pl.snapshot_at DESC
    )
    SELECT k.sport_code, k.stat_type, k.player_name, k.threshold,
           k.yes_bid, k.yes_ask, s.line, s.over_payout
    FROM k
    JOIN s ON s.sport_code = k.sport_code
          AND s.stat_type  = k.stat_type
          AND lower(s.full_name) = lower(k.player_name)
          AND s.line = k.threshold - 0.5
    -- LEAK GUARD: pre-game Kalshi quotes only
    WHERE k.snapshot_at < s.game_datetime
""")


def compare(run_date: date) -> list[dict]:
    with engine.connect() as c:
        rows = c.execute(_SQL, {"d": run_date, "max_spread": MAX_SPREAD}
                         ).mappings().all()
    out = []
    for r in rows:
        mid = (r["yes_bid"] + r["yes_ask"]) / 2
        implied = 1.0 / r["over_payout"]
        out.append({**r, "kalshi_mid": round(mid, 4),
                    "sleeper_implied": round(implied, 4),
                    "diff": round(implied - mid, 4)})
    return out


def report(run_date: date) -> None:
    rows = compare(run_date)
    print(f"Sleeper-vs-Kalshi divergence — {run_date}  (matched props: {len(rows)})")
    if not rows:
        print("  nothing matched (no overlap, or no pre-game Kalshi snapshot that day)")
        return
    by_market: dict[tuple, list] = defaultdict(list)
    for r in rows:
        by_market[(r["sport_code"], r["stat_type"])].append(r)
    print(f"  {'market':24s} {'n':>4}  {'mean diff':>9}  {'MAE':>6}   (diff = sleeper_implied − kalshi_mid; +vig expected)")
    for (sp, st), rs in sorted(by_market.items()):
        diffs = [r["diff"] for r in rs]
        mean = sum(diffs) / len(diffs)
        mae = sum(abs(d) for d in diffs) / len(diffs)
        print(f"  {sp + '|' + st:24s} {len(rs):>4}  {mean:>+9.3f}  {mae:>6.3f}")
    print("  largest divergences:")
    for r in sorted(rows, key=lambda r: -abs(r["diff"]))[:10]:
        print(f"    {r['sport_code']}|{r['stat_type']:15s} {r['player_name']:22s}"
              f" o{r['line']:g}  sleeper {r['sleeper_implied']:.3f}"
              f" vs kalshi {r['kalshi_mid']:.3f}  diff {r['diff']:+.3f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default=None, help="slate date (YYYY-MM-DD)")
    args = ap.parse_args()
    d = (date.fromisoformat(args.date) if args.date
         else datetime.now(ZoneInfo("America/Los_Angeles")).date())
    report(d)


if __name__ == "__main__":
    main()
