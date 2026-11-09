"""NFL QB+own-WR stack logger — the correlated-parlay lane, graded forward.

The 2026-11-09 probe verdict (props.models.nfl_corr_probe, n=312 settled
stacks): QB passing-over × his own receiver's receiving-over hits jointly
0.381 vs 0.297 if independent (lift 1.29×). Pick'em books price parlay legs
independently, so that correlation is +EV without beating any single price —
IF the payout is big enough. At a 3.0× 2-pick it's +14.4%; at 2.5× it's −4.7%.
The edge lives or dies on the realized multiplier.

So this tool doesn't assume a flat multiplier: Sleeper posts PER-LEG payout
multipliers, a parlay pays their product, and each logged stack carries the
product of its two legs' posted over-multipliers. The forward ROI is then
graded at that stored payout — the number the book actually offered.

⚠️ THE ONE UNVERIFIED ASSUMPTION: books restrict obviously-correlated combos.
If Sleeper blocks QB+own-WR in-app, or reprices the pairing below the posted
leg product, the stored payout overstates and this lane is dead on arrival.
That is checkable only in the app with an account (the API carries no
restriction metadata — verified 2026-11-09, `options[].metadata` is empty).
Until confirmed, the ROI here is an upper bound on a REAL track record.

Honest-lane rules (same as sleeper_arb): stacks are logged PRE-GAME only
(+30min guard), every available stack is logged (the edge claim is the
correlation itself, not per-stack cleverness — cherry-picking would re-smuggle
a model into a model-independent lane), grading is played-only for both legs,
and the verdict is MIN_TIER_N-gated. No Discord: an unproven lane doesn't
advertise picks (the arb lane's lesson); ROI accrues silently via --roi.

Run:  python -m props.picks.nfl_stacks           # log today's upcoming stacks
      python -m props.picks.nfl_stacks --roi      # forward realized-ROI report
      python -m props.picks.nfl_stacks --date 2026-11-15
"""
import argparse
from datetime import datetime, date
from zoneinfo import ZoneInfo

from sqlalchemy import text

from props.utils.db import session_scope, engine
from props.utils.logging import log, configure_logging
from props.models.nfl_corr_probe import probe, MIN_PAIRS
from props.models.odds_track import MIN_TIER_N


def _load_legs(session, run_date):
    """Today's upcoming Sleeper NFL passing/receiving overs with payouts.

    Team comes from players.current_team_id (rosters ingest) — pre-game there
    is no player_games row to read it from.
    """
    return session.execute(text("""
        SELECT DISTINCT ON (pl.player_id, pl.stat_type)
               pl.player_id, p.full_name AS player_name, p.current_team_id AS team_id,
               pl.game_id, pl.stat_type, pl.line_value::float AS line,
               pl.over_payout::float AS over_payout
        FROM prop_lines pl
        JOIN players p USING (player_id)
        JOIN games g ON g.game_id = pl.game_id
        WHERE pl.sportsbook = 'sleeper' AND g.sport_code = 'nfl'
          AND pl.stat_type IN ('passing_yards', 'receiving_yards')
          AND pl.line_variant = 'standard' AND pl.over_payout IS NOT NULL
          AND g.game_date = :d
          -- LEAK GUARD: only games that haven't started (+30m buffer), so a
          -- mid-slate run can never log a stack whose outcome is underway.
          AND g.game_datetime > NOW() + INTERVAL '30 minutes'
        ORDER BY pl.player_id, pl.stat_type, pl.snapshot_at DESC
    """), {"d": run_date}).all()


def build_stacks(legs, joint_est: float | None) -> list[dict]:
    """Pair each QB (passing_yards) with every SAME-TEAM, SAME-GAME receiver
    (receiving_yards). Pure function so the pairing logic is unit-testable."""
    qbs = [r for r in legs if r.stat_type == "passing_yards"]
    recs = [r for r in legs if r.stat_type == "receiving_yards"]
    stacks = []
    for qb in qbs:
        if qb.team_id is None:
            continue
        for rec in recs:
            if rec.game_id != qb.game_id or rec.team_id != qb.team_id \
                    or rec.player_id == qb.player_id:
                continue
            payout = round(qb.over_payout * rec.over_payout, 4)
            stacks.append({
                "game_id": qb.game_id, "team_id": qb.team_id,
                "qb_player_id": qb.player_id, "qb_name": qb.player_name,
                "wr_player_id": rec.player_id, "wr_name": rec.player_name,
                "qb_line": round(qb.line, 2), "wr_line": round(rec.line, 2),
                "qb_payout": round(qb.over_payout, 4),
                "wr_payout": round(rec.over_payout, 4),
                "payout": payout,
                "joint_est": round(joint_est, 4) if joint_est is not None else None,
                "ev_est": round(joint_est * payout - 1.0, 4) if joint_est is not None else None,
            })
    stacks.sort(key=lambda s: s["ev_est"] if s["ev_est"] is not None else -1.0,
                reverse=True)
    return stacks


_DB_KEYS = ("game_id", "team_id", "qb_player_id", "wr_player_id", "qb_line",
            "wr_line", "qb_payout", "wr_payout", "payout", "joint_est", "ev_est")


def _persist(session, run_date, stacks):
    session.execute(text("DELETE FROM nfl_stacks WHERE run_date = :d"), {"d": run_date})
    if not stacks:
        return 0
    session.execute(text("""
        INSERT INTO nfl_stacks (run_date, game_id, team_id, qb_player_id, wr_player_id,
            qb_line, wr_line, qb_payout, wr_payout, payout, joint_est, ev_est)
        VALUES (:d, :game_id, :team_id, :qb_player_id, :wr_player_id,
            :qb_line, :wr_line, :qb_payout, :wr_payout, :payout, :joint_est, :ev_est)
        ON CONFLICT (run_date, qb_player_id, wr_player_id) DO UPDATE SET
            qb_line=EXCLUDED.qb_line, wr_line=EXCLUDED.wr_line,
            qb_payout=EXCLUDED.qb_payout, wr_payout=EXCLUDED.wr_payout,
            payout=EXCLUDED.payout, joint_est=EXCLUDED.joint_est,
            ev_est=EXCLUDED.ev_est, created_at=NOW()
    """), [{**{k: st[k] for k in _DB_KEYS}, "d": run_date} for st in stacks])
    return len(stacks)


def run(run_date: date | None = None):
    configure_logging()
    if run_date is None:
        run_date = datetime.now(ZoneInfo("America/Los_Angeles")).date()
    # trailing measured joint over·over rate — population estimate, display/EV
    # context only (every stack is logged regardless; see module docstring)
    s = probe()
    joint = s["joint"] if s and s.get("n", 0) >= MIN_PAIRS else None
    with session_scope() as sess:
        legs = _load_legs(sess, run_date)
        stacks = build_stacks(legs, joint)
        _persist(sess, run_date, stacks)
    log.info("nfl_stacks_logged", date=run_date.isoformat(), legs=len(legs),
             stacks=len(stacks), joint_est=joint,
             top_payout=(stacks[0]["payout"] if stacks else None))
    for st in stacks[:8]:
        ev = f"  ev_est {st['ev_est']:+.1%}" if st["ev_est"] is not None else ""
        print(f"  {st['qb_name']} o{st['qb_line']:g} pass + {st['wr_name']} "
              f"o{st['wr_line']:g} rec  @ {st['payout']:.2f}x{ev}")


# ── forward realized-ROI tracker ─────────────────────────────────────────────
def stacks_roi() -> dict:
    """Realized ROI of logged stacks at their STORED payout products. A stack
    wins iff BOTH actuals clear their lines; played-only for both legs."""
    with engine.connect() as c:
        # NFL box scores are written per stat group (absent key = real zero for
        # a player WITH a row — see settle_picks.ZERO_WHEN_ABSENT), so a
        # receiver with no catches grades as a LOSS vs his line, not as
        # unsettled. Requiring the key instead would bias ROI upward.
        rows = c.execute(text("""
            SELECT s.payout::float AS payout,
                   s.qb_line::float AS qb_line, s.wr_line::float AS wr_line,
                   COALESCE((qg.stats->>'passing_yards')::float, 0) AS qb_actual,
                   COALESCE((wg.stats->>'receiving_yards')::float, 0) AS wr_actual
            FROM nfl_stacks s
            JOIN player_games qg ON qg.player_id = s.qb_player_id AND qg.game_id = s.game_id
            JOIN player_games wg ON wg.player_id = s.wr_player_id AND wg.game_id = s.game_id
            WHERE qg.stats <> '{}'::jsonb AND wg.stats <> '{}'::jsonb
              AND COALESCE(qg.did_play, true) AND COALESCE(wg.did_play, true)
        """)).mappings().all()
    rets = [(r["payout"] - 1.0) if (r["qb_actual"] > r["qb_line"] and
                                    r["wr_actual"] > r["wr_line"]) else -1.0
            for r in rows]
    n = len(rets)
    if n == 0:
        return {"n": 0, "roi": 0.0, "lo": 0.0, "hi": 0.0, "hit": 0.0, "verdict": "—"}
    roi = sum(rets) / n
    se = (sum((x - roi) ** 2 for x in rets) / n / n) ** 0.5 if n > 1 else 0.0
    lo, hi = roi - 1.96 * se, roi + 1.96 * se
    hit = sum(1 for ret in rets if ret > 0) / n
    verdict = ("building" if n < MIN_TIER_N else "profitable" if lo > 0
               else "losing" if hi < 0 else "not proven")
    return {"n": n, "roi": roi, "lo": lo, "hi": hi, "hit": hit, "verdict": verdict}


def _print_roi():
    s = stacks_roi()
    print("NFL QB+own-WR stacks (forward, at stored payout products):")
    if s["n"] >= MIN_TIER_N:
        print(f"  n={s['n']:>4}  ROI {s['roi']:+.1%}  [{s['lo']:+.1%}, {s['hi']:+.1%}]  "
              f"hit {s['hit']:.1%}  →  {s['verdict']}")
    elif s["n"]:
        print(f"  building ({s['n']}/{MIN_TIER_N} settled) — verdict held until the tier fills")
    else:
        print("  no settled stacks yet")
    print("  ⚠️ upper bound until the in-app combo check is done (see module docstring)")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--roi", action="store_true", help="print the forward realized-ROI report")
    p.add_argument("--date", default=None, help="run for a specific date (YYYY-MM-DD)")
    args = p.parse_args()
    if args.roi:
        configure_logging()
        _print_roi()
        return
    run(run_date=date.fromisoformat(args.date) if args.date else None)


if __name__ == "__main__":
    main()
