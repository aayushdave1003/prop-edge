# prop-edge track-record provenance — why the headline moved:
#   72.0%  in-sample cutoff leak (backtest saw the test window)
#   56.4%  cutoffs fixed, but still included 330 lookahead picks
#   50.3%  forward-only, point-in-time, clean  <- the real number
# The one bucket that looked real (mlb|hits|under 85.9%) was a single
# backfill batch of already-played games. Not a forward edge.
"""Reproduce, from the prod DB, the five findings that killed the mlb|hits|under
"edge" — so the mirage is auditable, not a claim. Run:

    DATABASE_URL=$RAILWAY_DATABASE_URL python -m props.models.mirage_analysis_mlb_hits_under

Each finding prints its own numbers. Nothing here is fit, tuned, or selected —
it only counts and dates the picks in the bucket.
"""
from __future__ import annotations

from collections import Counter

from sqlalchemy import text

from props.utils.db import engine, db_banner


_SQL = text("""
    SELECT pk.pick_id,
           pk.picked_at,
           g.game_datetime,
           g.game_date,
           (pk.picked_at AT TIME ZONE 'America/Los_Angeles')::date AS decided,
           pk.leg_result,
           pl.line_value
    FROM picks pk
    JOIN games g USING (game_id)
    LEFT JOIN prop_lines pl ON pl.line_id = pk.line_id
    WHERE g.sport_code = 'mlb' AND pk.stat_type = 'hits' AND pk.direction = 'under'
      AND pk.leg_result IN ('win', 'loss')
    ORDER BY pk.picked_at, pk.pick_id
""")


def load() -> list[dict]:
    with engine.connect() as c:
        return [dict(r) for r in c.execute(_SQL).mappings().all()]


def main() -> int:
    print(db_banner())
    rows = load()
    n = len(rows)
    print(f"\nmlb|hits|under — {n} settled picks\n")

    # 1. No temporal spread: nearly the whole bucket shares ONE insert.
    by_date = Counter(str(r["decided"]) for r in rows)
    by_ts = Counter(str(r["picked_at"])[:19] for r in rows)
    top_date, top_date_n = by_date.most_common(1)[0]
    top_ts, top_ts_n = by_ts.most_common(1)[0]
    print("1. NO TEMPORAL SPREAD")
    print(f"   {top_date_n}/{n} picks share a single decision date ({top_date}); "
          f"{top_ts_n}/{n} share a single insert timestamp ({top_ts}).")
    print(f"   distinct decision dates in the whole bucket: {len(by_date)}")

    # 2. Monthly counts — one spike, then nothing.
    by_month = Counter(str(r["decided"])[:7] for r in rows)
    print("\n2. MONTHLY COUNTS (a burst, not a stream)")
    for m in sorted(by_month):
        print(f"   {m}: {by_month[m]}")

    # 3. Forward-only picks + how many even have a line.
    fwd = [r for r in rows if r["game_datetime"] is not None and r["picked_at"] < r["game_datetime"]]
    fwd_null_line = [r for r in fwd if r["line_value"] is None]
    print("\n3. LINE DATA MISSING ON THE CLEAN PICKS")
    print(f"   forward-only (picked_at < game start): {len(fwd)}/{n}")
    print(f"   of those, null line_value (no prop_lines link): {len(fwd_null_line)}/{len(fwd)}")

    # 4. Capacity the live pipeline actually produces (drop the backfill batch).
    # Honest rate = live picks over the WHOLE window since the batch (including
    # the weeks it produced nothing) — not just the span where picks happen to
    # exist, which would flatter the number by ignoring the dry stretch.
    from datetime import date
    batch_date = Counter(r["decided"] for r in rows).most_common(1)[0][0]
    live = [r for r in rows if r["decided"] != batch_date]
    weeks_since = max(1.0, (date.today() - batch_date).days / 7)
    print("\n4. CAPACITY (post-backfill, i.e. the real live stream)")
    print(f"   picks outside the {batch_date} batch: {len(live)} over the "
          f"{weeks_since:.1f} weeks since -> ~{len(live)/weeks_since:.1f} picks/week (< 1/wk = a paper)")

    # 5. Lookahead: logged after the game had started.
    after = [r for r in rows if r["game_datetime"] is not None and r["picked_at"] >= r["game_datetime"]]
    print("\n5. LOOKAHEAD (logged after first pitch — outcome partly known)")
    print(f"   picked_at >= game_datetime: {len(after)}/{n}")

    print("\nVERDICT: a single backfill batch of already-played games — not a forward edge.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
