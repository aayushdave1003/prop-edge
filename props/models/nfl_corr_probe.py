"""NFL correlated-parlay probe — QUEUED to run in-season.

A QB's passing_yards and his OWN receiver's receiving_yards are *mechanically*
correlated (the same passing plays produce both), unlike MLB same-team batters
whose overs share only the game environment (~3% lift — too small to beat the
parlay vig, see the July-2026 corr_probe). If this QB+WR correlation is large
enough to clear Sleeper's 2-pick hold, a QB+own-receiver STACK is +EV without
beating any single price — the one honest shot at profitability left.

Self-gates on sample size (off-season / early season have no data), so it's safe
to run daily; it only speaks up once there's a real sample. daily.sh calls it
weekly in-season (Mondays, has_recent_games nfl).

Run:  python -m props.models.nfl_corr_probe [--post]
"""
import argparse

import requests
from sqlalchemy import text

from props.utils.db import engine
from props.utils.config import settings
from props.utils.logging import log, configure_logging

MIN_PAIRS = 150       # need a real sample (≈ mid-season) before any verdict
PARLAY_PAYOUTS = (3.0, 2.5)   # Sleeper 2-pick multipliers to test the +EV hurdle against


def _load():
    import pandas as pd
    return pd.read_sql(text("""
        SELECT DISTINCT ON (pl.player_id, pl.game_id, pl.stat_type)
               pl.game_id, pg.team_id, pl.player_id, pl.stat_type,
               pl.line_value::float AS line, (pg.stats->>pl.stat_type)::float AS actual
        FROM prop_lines pl
        JOIN player_games pg ON pg.player_id=pl.player_id AND pg.game_id=pl.game_id
        JOIN games g ON g.game_id=pl.game_id
        WHERE pl.sportsbook='sleeper' AND g.sport_code='nfl'
          AND pl.stat_type IN ('passing_yards','receiving_yards')
          AND pl.line_variant='standard' AND pg.stats ? pl.stat_type
          AND pl.line_value IS NOT NULL
        ORDER BY pl.player_id, pl.game_id, pl.stat_type, pl.snapshot_at DESC
    """), engine)


def probe() -> dict | None:
    """QB passing-over × own-receiver receiving-over, same game+team. Returns the
    correlation lift + the +EV verdict vs the parlay vig, or None if no NFL data."""
    df = _load()
    if len(df) == 0:
        return None
    df["over"] = (df["actual"] > df["line"]).astype(int)
    qb = (df[df.stat_type == "passing_yards"][["game_id", "team_id", "over"]]
          .rename(columns={"over": "qb_over"}))
    rec = (df[df.stat_type == "receiving_yards"][["game_id", "team_id", "player_id", "over"]]
           .rename(columns={"over": "rec_over"}))
    stack = qb.merge(rec, on=["game_id", "team_id"])        # QB × each own receiver
    n = len(stack)
    if n == 0:
        return {"n": 0}
    m_qb = float(qb["qb_over"].mean())
    m_rec = float(rec["rec_over"].mean())
    indep = m_qb * m_rec
    joint = float(((stack["qb_over"] == 1) & (stack["rec_over"] == 1)).mean())
    lift = joint / indep if indep else 0.0
    ev = {pay: joint * pay - 1 for pay in PARLAY_PAYOUTS}
    clears = any(joint * pay > 1 for pay in PARLAY_PAYOUTS)
    return {"n": n, "m_qb": m_qb, "m_rec": m_rec, "indep": indep,
            "joint": joint, "lift": lift, "ev": ev, "clears": clears}


def _post_discord(s: dict):
    if not settings.discord_webhook_url:
        return
    if s["n"] < MIN_PAIRS:
        desc = (f"**Building** — {s['n']}/{MIN_PAIRS} settled QB+WR stacks. "
                "Correlation verdict holds until the sample is real.")
        color = 0x9A9AA8
    else:
        edge = "\n\n**Clears the parlay vig — a real +EV stack.** ✅" if s["clears"] else \
               "\n\nCorrelation is real but **doesn't clear the parlay vig** (like MLB) — not +EV."
        desc = (f"QB passing-over × own-WR receiving-over, same game (n={s['n']}):\n"
                f"joint **{s['joint']:.3f}** vs independence {s['indep']:.3f} → "
                f"**lift {s['lift']:.2f}×**\n"
                f"2-pick @3x EV {s['ev'][3.0]:+.1%} · @2.5x EV {s['ev'][2.5]:+.1%}{edge}")
        color = 0x2ECC71 if s["clears"] else 0xE67E22
    payload = {"embeds": [{"title": "🏈 NFL QB+WR correlation probe", "description": desc,
                           "color": color, "footer": {"text": "correlated-parlay edge test · paper"}}]}
    try:
        requests.post(settings.discord_webhook_url, json=payload, timeout=10)
    except Exception as e:
        log.warning("nfl_corr_post_failed", error=str(e)[:120])


def main():
    configure_logging()
    p = argparse.ArgumentParser()
    p.add_argument("--post", action="store_true", help="post the verdict to Discord")
    args = p.parse_args()
    s = probe()
    if s is None:
        print("NFL QB+WR probe: no NFL prop data yet — waiting for the season.")
        return
    if s["n"] < MIN_PAIRS:
        print(f"NFL QB+WR probe: building ({s['n']}/{MIN_PAIRS} stacks) — verdict held until the sample is real.")
    else:
        print(f"NFL QB+WR probe: n={s['n']}  joint={s['joint']:.3f} vs indep {s['indep']:.3f}  "
              f"lift={s['lift']:.2f}x  @3x EV {s['ev'][3.0]:+.1%}  -> "
              f"{'CLEARS the vig (+EV)' if s['clears'] else 'does not clear vig'}")
    if args.post:
        _post_discord(s)


if __name__ == "__main__":
    main()
