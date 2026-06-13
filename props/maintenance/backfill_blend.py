"""One-time (idempotent) backfill of the model/market blend onto historical picks.

After migration 0008, ``picks.model_prob`` holds the BLENDED probability and
``model_prob_raw`` the original model output. Historical picks predate this, so
their ``model_prob`` is still the raw value. This script puts the whole history
on the blended scale, so the cutoffs / calibration / backtest (which read
``model_prob``) are consistent across old and new picks:

  1. model_prob_raw := model_prob   (preserve the raw output; only where unset)
  2. market_prob     := the real no-vig prob for the pick's side, from market_odds
                        (NULL when no line — most picks)
  3. model_prob      := blend(sport, model_prob_raw, market_prob)

Only the picks with a real market line actually change; the rest blend to their
own raw value (no-op). Safe to re-run.

Run:  python -m props.maintenance.backfill_blend
"""
from sqlalchemy import text

from props.utils.db import session_scope
from props.utils.logging import log, configure_logging
from props.models.blend_weights import blend, load_weights


def run():
    configure_logging()
    weights = load_weights()
    with session_scope() as s:
        # 1. Preserve raw output (only the first time, so re-runs keep true raw).
        s.execute(text(
            "UPDATE picks SET model_prob_raw = model_prob WHERE model_prob_raw IS NULL"))

        # 2. Real market-implied prob for each pick's side, from market_odds.
        s.execute(text("""
            UPDATE picks pk SET market_prob = sub.implied
            FROM (
                SELECT pk2.pick_id,
                       CASE WHEN pk2.direction = 'over'
                            THEN AVG(mo.market_over_prob)
                            ELSE 1 - AVG(mo.market_over_prob) END AS implied
                FROM picks pk2
                JOIN market_odds mo
                  ON mo.player_id = pk2.player_id AND mo.game_id = pk2.game_id
                WHERE mo.market_over_prob IS NOT NULL
                GROUP BY pk2.pick_id, pk2.direction
            ) sub
            WHERE pk.pick_id = sub.pick_id
        """))

        # 3. Recompute model_prob = blend(raw, market) per-sport in Python.
        rows = s.execute(text("""
            SELECT pick_id, sport_code, model_prob_raw::float AS raw,
                   market_prob::float AS mkt
            FROM picks WHERE model_prob_raw IS NOT NULL
        """)).all()
        changed = 0
        for r in rows:
            blended = round(blend(r.sport_code, r.raw, r.mkt, weights), 4)
            if abs(blended - r.raw) > 1e-9:        # only write the ones that move
                s.execute(text("UPDATE picks SET model_prob = :mp WHERE pick_id = :pid"),
                          {"mp": blended, "pid": r.pick_id})
                changed += 1
        n_market = sum(1 for r in rows if r.mkt is not None)
    log.info("backfill_blend_done", picks=len(rows), with_market_line=n_market,
             model_prob_changed=changed)
    print(f"backfilled {len(rows)} picks: {n_market} have a market line, "
          f"{changed} model_prob values blended/changed")


if __name__ == "__main__":
    run()
