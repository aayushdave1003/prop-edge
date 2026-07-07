"""Read model picks from Postgres and shape them into the pick-board contract.

Mirrors the Streamlit dashboard's computations field-for-field so the board shows
the SAME numbers the rest of prop-edge does — confidence (calibrated prob), likely
range (interval calibration), paper Kelly (expected_value), recent form (graded
actuals), weather, insight, the diversified Top-Slate + paper slate-Kelly stakes,
and game predictions. No model math is re-derived here; this only reads + formats.

RESEARCH / PAPER-TRACKING: money sizing is hypothetical, never betting advice.
"""
from __future__ import annotations

import time

from sqlalchemy import text

from props.utils.db import engine
from props.api.formatting import stat_label, league_label

# ── reusable pipeline helpers (graceful fallback if anything is unavailable) ──
try:
    from props.models.prob_calibration import calibrate
except Exception:
    def calibrate(p, params=None):  # type: ignore
        return float(p)

try:
    from props.models.interval_calibration import empirical_interval
except Exception:
    def empirical_interval(stat, mean):  # type: ignore
        return None

try:
    from props.models.category_cutoffs import rec_cutoff, load_cutoffs, compute_from_db
    try:
        _CUTOFFS = compute_from_db(engine)
    except Exception:
        _CUTOFFS = load_cutoffs()
except Exception:
    rec_cutoff = None
    _CUTOFFS = {}

# The Performance view's recommended-tier rate is the AUDITED number: point-in-time
# (each cutoff sees only prior settlements) and forward-only (no lookahead). We
# reuse the honest_oos harness verbatim so the UI can't drift from the truth. If
# it can't import, fetch_performance refuses to run rather than fall back to the
# in-sample headline it replaced.
try:
    from props.models.honest_oos import walk_forward_oos, wilson_ci
    from props.models.category_cutoffs import BREAKEVEN
    _HONEST_OK = True
except Exception:
    _HONEST_OK = False
    BREAKEVEN = 0.577  # per-leg 2-pick parlay breakeven (1/√3)

# every league we know about; calendar-gated ones show as "soon" in the UI
KNOWN_LEAGUES = ["mlb", "wnba", "nba", "nhl", "nfl", "cfb", "cbb", "soccer"]

# combo-stat actual expressions (mirror of dashboard.COMBO_STAT_SQL) for form grading
COMBO_STAT_SQL = {
    "pts_rebs_asts": "COALESCE((pg.stats->>'points')::float,0)+COALESCE((pg.stats->>'rebounds')::float,0)+COALESCE((pg.stats->>'assists')::float,0)",
    "pts_rebs": "COALESCE((pg.stats->>'points')::float,0)+COALESCE((pg.stats->>'rebounds')::float,0)",
    "pts_asts": "COALESCE((pg.stats->>'points')::float,0)+COALESCE((pg.stats->>'assists')::float,0)",
    "rebs_asts": "COALESCE((pg.stats->>'rebounds')::float,0)+COALESCE((pg.stats->>'assists')::float,0)",
    "blocks_steals": "COALESCE((pg.stats->>'blocks')::float,0)+COALESCE((pg.stats->>'steals')::float,0)",
    "threes_made": "COALESCE((pg.stats->>'fg3_made')::float,(pg.stats->>'threes_made')::float,0)",
    "home_runs": "COALESCE((pg.stats->>'home_runs')::float,0)",
}
import os

# Board date: defaults to "today" in Pacific (matches the dashboard). Set
# BOARD_DATE=YYYY-MM-DD to view a specific past slate (handy before the morning
# pipeline runs, or to demo a settled day) — bound as a param, never interpolated.
_BOARD_DATE = os.getenv("BOARD_DATE")
TODAY = ":bdate" if _BOARD_DATE else "(NOW() AT TIME ZONE 'America/Los_Angeles')::date"


def _p(extra: dict | None = None) -> dict:
    d = dict(extra or {})
    if _BOARD_DATE:
        d["bdate"] = _BOARD_DATE
    return d


def _cut(sport, stat, direction, prob):
    if rec_cutoff is None or prob is None:
        return 0.57
    try:
        return float(rec_cutoff(sport, stat, table=_CUTOFFS, direction=direction))
    except Exception:
        return 0.57


def _likely_range(stat_key: str, mean: float) -> str:
    band = empirical_interval(stat_key, mean)
    if band is None:
        try:
            from scipy.stats import poisson
            band = (int(poisson.ppf(0.25, mean)), int(poisson.ppf(0.75, mean)))
        except Exception:
            band = (int(mean), int(round(mean)))
    return f"{band[0]}–{band[1]}"


def _weather(temp_f, wind_out_mph, is_dome) -> dict | None:
    if is_dome:
        return {"temp_f": None, "note": "dome (neutral)"}
    if wind_out_mph is None:
        return None
    wo = float(wind_out_mph)
    note = f"wind out +{wo:.0f}" if wo >= 5 else f"wind in {wo:.0f}" if wo <= -5 else "calm"
    return {"temp_f": int(temp_f) if temp_f is not None else None, "note": note}


_PICKS_SQL = """
    SELECT
        pk.pick_id, pk.player_id, pk.game_id, p.current_team_id AS team_id,
        g.sport_code,
        p.full_name AS player, p.photo_url AS photo_url,
        t.abbreviation AS team,
        pk.stat_type, pl.line_value AS line, pk.direction,
        pk.model_prob, pr.predicted_mean,
        COALESCE(pk.market_edge, pk.edge) AS edge_frac,
        pk.expected_value AS kelly, pk.line_movement, pk.line_open,
        g.game_datetime,
        ht.abbreviation AS home_team, at.abbreviation AS away_team,
        wx.temp_f AS wx_temp, wx.wind_out_mph AS wx_wind_out, wx.is_dome AS wx_dome
    FROM picks pk
    JOIN players p USING (player_id)
    LEFT JOIN teams t ON t.team_id = p.current_team_id
    JOIN games g USING (game_id)
    JOIN prop_lines pl ON pl.line_id = pk.line_id
    LEFT JOIN teams ht ON ht.team_id = g.home_team_id
    LEFT JOIN teams at ON at.team_id = g.away_team_id
    LEFT JOIN game_weather wx ON wx.game_id = pk.game_id
    LEFT JOIN predictions pr ON pr.prediction_id = pk.prediction_id
    WHERE (pk.picked_at AT TIME ZONE 'America/Los_Angeles')::date = {today}
      AND g.game_date = {today}
      AND (pk.leg_result IS NULL OR pk.leg_result != 'void')
      AND pr.predicted_mean IS NOT NULL AND pl.line_value IS NOT NULL
      {league_filter} {stats_filter} {dir_filter}
    ORDER BY COALESCE(pk.market_edge, pk.edge) DESC
""".replace("{today}", TODAY)


def _load_form(conn, pairs: list[tuple]) -> dict:
    """pairs = [(player_id, stat_key), ...]; returns {(player_id,stat_key): [actual,...]}
    most-recent-first (last 10 final games before today)."""
    out: dict = {}
    by_stat: dict[str, list[int]] = {}
    for pid, stat in pairs:
        by_stat.setdefault(stat, []).append(pid)
    for stat, pids in by_stat.items():
        expr = COMBO_STAT_SQL.get(stat, f"(pg.stats->>'{stat}')::float")
        sql = text(f"""
            SELECT player_id, actual FROM (
                SELECT pg.player_id, {expr} AS actual,
                       ROW_NUMBER() OVER (PARTITION BY pg.player_id ORDER BY g.game_date DESC) AS rn
                FROM player_games pg JOIN games g USING (game_id)
                WHERE pg.player_id = ANY(:pids) AND g.status = 'final'
                  AND g.game_date < CURRENT_DATE
            ) s WHERE rn <= 10 ORDER BY player_id, rn
        """)
        for r in conn.execute(sql, {"pids": list(set(pids))}).mappings():
            if r["actual"] is None:
                continue
            out.setdefault((r["player_id"], stat), []).append(float(r["actual"]))
    return out


def _grade_form(actuals: list[float], line: float, direction: str):
    """Return (form[bool|None recent-first], 'h/d' L5, 'h/d' L10) graded by the
    pick's lean: a game 'hits' if the lean would have cashed (push on exact line)."""
    def grade(a):
        if a == line:
            return None
        return (a > line) if direction == "over" else (a < line)

    graded = [grade(a) for a in actuals]  # recent-first

    def hd(window):
        w = graded[:window]
        hits = sum(1 for x in w if x is True)
        den = sum(1 for x in w if x is not None)
        return f"{hits}/{den}" if den else "0/0"

    return graded[:5], hd(5), hd(10)


def _insight(l5: str, badge: str, edge_frac, line_mv, direction, prob) -> str:
    bits = []
    try:
        h, d = (int(x) for x in l5.split("/"))
    except Exception:
        h, d = 0, 0
    if d >= 3 and h / d >= 0.6:
        bits.append(f"hit {badge} {h}/{d} last 5")
    if edge_frac is not None and float(edge_frac) >= 0.05:
        bits.append(f"+{float(edge_frac) * 100:.0f}% vs market")
    if line_mv is not None and abs(float(line_mv)) >= 0.05:
        mv = float(line_mv)
        if (mv > 0 and direction == "over") or (mv < 0 and direction == "under"):
            bits.append("line moving your way")
    if not bits:
        bits.append(f"model {calibrate(prob):.0%} confident")
    return " · ".join(bits[:3])


# ── honest paper-Kelly gate ──────────────────────────────────────────────────
# Paper sizing (per-pick Kelly + slate stakes) is shown ONLY for categories with
# a demonstrated out-of-sample edge — the point-in-time recommended-tier Wilson
# 95% CI floor clears the 57.7% breakeven at a credible sample size. Today that
# set is EMPTY (no sport or category clears it), so every paper stake is 0. This
# is deliberate: a positive Kelly stake asserts a positive-EV bet, and we have
# not earned that claim. It lifts itself automatically if a category ever proves
# out. Reuses the audited honest_oos harness so it can't drift from the track
# record /api/performance reports.
_GATED_SETTLED_SQL = text("""
    SELECT g.sport_code AS sport, pk.stat_type, pk.direction,
           pk.model_prob, pk.leg_result,
           pk.market_prob, pk.market_prob_close,
           (pk.picked_at  AT TIME ZONE 'America/Los_Angeles')::date AS decided,
           (pk.settled_at AT TIME ZONE 'America/Los_Angeles')::date AS settled
    FROM picks pk
    JOIN games g USING (game_id)
    JOIN prop_lines pl ON pl.line_id = pk.line_id
    WHERE pk.leg_result IN ('win','loss') AND pk.model_prob IS NOT NULL
      AND g.game_datetime IS NOT NULL
      AND pk.picked_at < g.game_datetime   -- forward-only: no lookahead
      AND pl.line_value IS NOT NULL         -- valid-line-only: a real line existed
""")


def _load_gated_settled(conn) -> list[dict]:
    """Settled picks under the two source gates (forward-only + valid-line),
    shaped for honest_oos.walk_forward_oos. Shared by the honest track record and
    the paper-Kelly gate so the two can never disagree."""
    rows = conn.execute(_GATED_SETTLED_SQL).mappings().all()
    return [{
        "sport": r["sport"], "stat_type": r["stat_type"], "direction": r["direction"],
        "model_prob": float(r["model_prob"]),
        "win": 1 if r["leg_result"] == "win" else 0,
        "decided": r["decided"], "settled": r["settled"],
        "market_prob": r["market_prob"], "market_prob_close": r["market_prob_close"],
    } for r in rows]


_PROVEN_CACHE: dict = {"t": 0.0, "keys": None}
_PROVEN_TTL = 6 * 3600


def _proven_edge_keys() -> set:
    """Category keys with a demonstrated out-of-sample edge: the point-in-time
    recommended-tier Wilson 95% CI floor >= breakeven at a credible sample size.
    Keys are ``sport:<code>`` and ``cat:<sport>|<stat>|<dir>``. Cached 6h; empty
    today. Fail-closed (empty) on any error — the safe default is "no sizing"."""
    now = time.time()
    if _PROVEN_CACHE["keys"] is not None and now - _PROVEN_CACHE["t"] < _PROVEN_TTL:
        return _PROVEN_CACHE["keys"]
    keys: set = set()
    try:
        from collections import defaultdict
        from props.models.honest_oos import walk_forward_oos, wilson_ci
        from props.models.category_cutoffs import MIN_N_SPORT, MIN_N_STAT
        with engine.connect() as conn:
            rec = walk_forward_oos(_load_gated_settled(conn))
        bys: dict = defaultdict(list)
        bycat: dict = defaultdict(list)
        for r in rec:
            bys[r["sport"]].append(r)
            bycat[(r["sport"], r["stat_type"], r["direction"])].append(r)
        for sp, rs in bys.items():
            if len(rs) >= MIN_N_SPORT and wilson_ci(sum(x["win"] for x in rs), len(rs))[0] >= BREAKEVEN:
                keys.add(f"sport:{sp}")
        for (sp, st, dr), rs in bycat.items():
            if len(rs) >= MIN_N_STAT and wilson_ci(sum(x["win"] for x in rs), len(rs))[0] >= BREAKEVEN:
                keys.add(f"cat:{sp}|{st}|{dr}")
    except Exception:
        keys = set()
    _PROVEN_CACHE.update(t=now, keys=keys)
    return keys


def _is_proven(sport: str, stat: str, direction: str) -> bool:
    """True only if this pick's category has a demonstrated out-of-sample edge."""
    keys = _proven_edge_keys()
    return f"cat:{sport}|{stat}|{direction}" in keys or f"sport:{sport}" in keys


def fetch_picks(league=None, stats=None, direction=None, recommended_only=False) -> dict:
    params: dict = {}
    lf = sf = df = ""
    if league:
        lf = "AND g.sport_code = :league"
        params["league"] = league.lower()
    if stats:
        sf = "AND pk.stat_type = ANY(:stats)"
        params["stats"] = list(stats)
    if direction in ("over", "under"):
        df = "AND pk.direction = :direction"
        params["direction"] = direction
    sql = _PICKS_SQL.format(league_filter=lf, stats_filter=sf, dir_filter=df)

    with engine.connect() as conn:
        rows = conn.execute(text(sql), _p(params)).mappings().all()
        form_map = _load_form(conn, [(r["player_id"], r["stat_type"]) for r in rows])
        summary = _summary(conn)
        slate = _top_slate(conn)

    picks = []
    for r in rows:
        line = float(r["line"])
        proj = float(r["predicted_mean"])
        prob = float(r["model_prob"]) if r["model_prob"] is not None else None
        direction_v = r["direction"]
        recommended = prob is not None and prob >= _cut(r["sport_code"], r["stat_type"], direction_v, prob)
        if recommended_only and not recommended:
            continue
        actuals = form_map.get((r["player_id"], r["stat_type"]), [])
        form, l5, l10 = _grade_form(actuals, line, direction_v)
        rec = "more" if direction_v == "over" else "less"  # legacy alias not used by UI
        lean = "over" if direction_v == "over" else "under"
        badge = lean.upper()
        kelly = float(r["kelly"]) if r["kelly"] is not None else 0.0
        # No proven edge in this category → no paper sizing (honest: a positive
        # stake would assert a positive-EV bet we haven't earned).
        if not _is_proven(r["sport_code"], r["stat_type"], direction_v):
            kelly = 0.0
        picks.append({
            "id": str(r["pick_id"]),
            "league": r["sport_code"],
            "player": {
                "name": r["player"], "team": r["team"] or "",
                "headshot_url": r["photo_url"] or None, "team_logo_url": None,
                "watched": False,
            },
            "matchup": f"{r['away_team'] or '?'} @ {r['home_team'] or '?'}",
            "start_time": r["game_datetime"].isoformat() if r["game_datetime"] else None,
            "stat_type": stat_label(r["stat_type"]), "stat_key": r["stat_type"],
            "line": round(line, 2), "model_projection": round(proj, 2),
            "likely_range": _likely_range(r["stat_type"], proj),
            "edge_pct": round((proj - line) / line * 100, 1) if line else 0.0,
            "recommendation": lean, "_rec": rec, "recommended": recommended,
            "model_confidence": round(calibrate(prob) * 100) if prob is not None else 0,
            "kelly_pct": round(kelly * 100, 1),
            "weather": _weather(r["wx_temp"], r["wx_wind_out"], r["wx_dome"]),
            "form": form, "l5": l5, "l10": l10,
            "insight": _insight(l5, badge, r["edge_frac"], r["line_movement"], direction_v, prob),
        })

    return {"summary": summary, "top_slate": slate, "picks": picks}


def _summary(conn) -> dict:
    rows = conn.execute(text(f"""
        SELECT pk.sport_code, pk.stat_type, pk.direction, pk.model_prob,
               COALESCE(pk.market_edge, pk.edge) AS edge_frac
        FROM picks pk JOIN games g USING (game_id)
        WHERE (pk.picked_at AT TIME ZONE 'America/Los_Angeles')::date = {TODAY}
          AND g.game_date = {TODAY}
          AND (pk.leg_result IS NULL OR pk.leg_result != 'void')
    """), _p()).mappings().all()
    today = len(rows)
    rec = sum(1 for r in rows if r["model_prob"] is not None
              and float(r["model_prob"]) >= _cut(r["sport_code"], r["stat_type"], r["direction"], r["model_prob"]))
    edges = [float(r["edge_frac"]) for r in rows if r["edge_frac"] is not None]
    avg_edge = round(sum(edges) / len(edges) * 100, 1) if edges else 0.0

    wl = conn.execute(text("""
        SELECT leg_result, COUNT(*) n FROM picks
        WHERE picked_at >= NOW() - INTERVAL '7 days' AND leg_result IN ('win','loss')
        GROUP BY leg_result
    """)).mappings().all()
    w = next((int(x["n"]) for x in wl if x["leg_result"] == "win"), 0)
    loss = next((int(x["n"]) for x in wl if x["leg_result"] == "loss"), 0)
    wr = round(w / (w + loss) * 100) if (w + loss) else 0
    return {"today": today, "recommended": rec, "avg_edge_pct": avg_edge,
            "w": w, "l": loss, "win_rate_pct": wr}


def _top_slate(conn) -> dict | None:
    rows = conn.execute(text(f"""
        SELECT pk.pick_id, pk.player_id, pk.game_id, p.current_team_id AS team_id,
               g.sport_code, p.full_name AS player, pk.stat_type,
               pl.line_value AS line, pk.direction, pk.model_prob
        FROM picks pk JOIN players p USING (player_id) JOIN games g USING (game_id)
        JOIN prop_lines pl ON pl.line_id = pk.line_id
        WHERE (pk.picked_at AT TIME ZONE 'America/Los_Angeles')::date = {TODAY}
          AND g.game_date = {TODAY} AND (pk.leg_result IS NULL OR pk.leg_result != 'void')
          AND pk.model_prob IS NOT NULL AND pl.line_value IS NOT NULL
        ORDER BY pk.model_prob DESC
    """), _p()).mappings().all()
    # recommended only, then diversify: one per player, never 2 from (game, direction)
    recs = [r for r in rows if float(r["model_prob"]) >= _cut(r["sport_code"], r["stat_type"], r["direction"], r["model_prob"])]
    chosen, seen_players, seen_keys = [], set(), set()
    for r in recs:
        if r["player_id"] in seen_players:
            continue
        key = (r["game_id"], r["direction"])
        if key in seen_keys:
            continue
        chosen.append(r)
        seen_players.add(r["player_id"])
        seen_keys.add(key)
        if len(chosen) >= 4:
            break
    if len(chosen) < 2:
        return None

    n = len(chosen)
    payout = {2: 3.0, 3: 5.0, 4: 10.0}.get(n, 3.0)
    joint = 1.0
    for r in chosen:
        joint *= calibrate(float(r["model_prob"]))
    games = len({r["game_id"] for r in chosen})

    stakes = None
    try:
        from props.picks.slate_kelly import slate_kelly_stakes, MAX_STAKE_PER_PICK
        legs_in = [{"model_prob": float(r["model_prob"]), "player_id": r["player_id"],
                    "game_id": r["game_id"], "team_id": r["team_id"],
                    "direction": r["direction"], "stat_type": r["stat_type"]} for r in chosen]
        stakes = [round(float(s) * 100, 1) for s in slate_kelly_stakes(legs_in)]
        # Gate: paper sizing only for proven-edge categories (none today → all
        # None). Same honest rule as per-pick Kelly.
        proven_mask = [_is_proven(r["sport_code"], r["stat_type"], r["direction"]) for r in chosen]
        stakes = [s if proven_mask[i] else None for i, s in enumerate(stakes)]
        max_stake = round(MAX_STAKE_PER_PICK * 100) if any(proven_mask) else None
    except Exception:
        max_stake = None

    legs = []
    for i, r in enumerate(chosen):
        legs.append({
            "player": r["player"], "league": r["sport_code"],
            "stat_type": stat_label(r["stat_type"]), "line": float(r["line"]),
            "confidence": round(calibrate(float(r["model_prob"])) * 100),
            "recommendation": "over" if r["direction"] == "over" else "under",
            "stake_pct": stakes[i] if stakes else None,
        })
    return {"n": n, "payout": payout, "games": games,
            "joint_hit_pct": round(joint * 100), "max_stake_pct": max_stake, "legs": legs}


def fetch_leagues() -> list[dict]:
    """All known leagues; ones with picks today are `available`, the rest 'soon'."""
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT g.sport_code, pk.stat_type, COUNT(*) n
            FROM picks pk JOIN games g USING (game_id)
            JOIN prop_lines pl ON pl.line_id = pk.line_id
            LEFT JOIN predictions pr ON pr.prediction_id = pk.prediction_id
            WHERE (pk.picked_at AT TIME ZONE 'America/Los_Angeles')::date = {TODAY}
              AND g.game_date = {TODAY} AND (pk.leg_result IS NULL OR pk.leg_result != 'void')
              AND pr.predicted_mean IS NOT NULL AND pl.line_value IS NOT NULL
            GROUP BY g.sport_code, pk.stat_type ORDER BY g.sport_code, n DESC
        """), _p()).mappings().all()

    live: dict[str, dict] = {}
    for r in rows:
        sc = r["sport_code"]
        lg = live.setdefault(sc, {"code": sc, "label": league_label(sc), "count": 0,
                                  "available": True, "stats": []})
        lg["count"] += int(r["n"])
        lg["stats"].append({"key": r["stat_type"], "label": stat_label(r["stat_type"]), "count": int(r["n"])})

    ordered = sorted(live.values(), key=lambda x: x["count"], reverse=True)
    have = set(live)
    for code in KNOWN_LEAGUES:
        if code not in have:
            ordered.append({"code": code, "label": league_label(code), "count": 0,
                            "available": False, "stats": []})
    return ordered


def fetch_games(league=None) -> list[dict]:
    """Game predictions from games.context (winner model output), today only."""
    params = {}
    lf = ""
    if league:
        lf = "AND g.sport_code = :league"
        params["league"] = league.lower()
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT g.sport_code,
                   ht.name AS home, at.name AS away,
                   (g.context->>'home_win_prob')::float AS home_wp,
                   g.context->>'implied_margin' AS margin,
                   g.context->>'home_pitcher' AS home_sp,
                   g.context->>'away_pitcher' AS away_sp
            FROM games g
            JOIN teams ht ON ht.team_id = g.home_team_id
            JOIN teams at ON at.team_id = g.away_team_id
            WHERE g.game_date = {TODAY} AND g.context ? 'home_win_prob' {lf}
            ORDER BY g.sport_code
        """), _p(params)).mappings().all()

    out = []
    for r in rows:
        hwp = float(r["home_wp"]) if r["home_wp"] is not None else 0.5
        home_fav = hwp >= 0.5
        fav = r["home"] if home_fav else r["away"]
        margin = None
        if r["margin"] is not None:
            try:
                margin = f"{fav} -{abs(float(r['margin'])):.1f}"
            except Exception:
                margin = None
        starters = None
        if r["home_sp"] or r["away_sp"]:
            starters = {"home": r["home_sp"] or "TBD", "away": r["away_sp"] or "TBD"}
        out.append({
            "home": r["home"], "away": r["away"],
            "home_win_pct": round(hwp, 3), "away_win_pct": round(1 - hwp, 3),
            "model_pick": fav, "implied_line": margin, "starters": starters,
        })
    return out


# ── Performance (settled-pick track record) + Soft Lines ─────────────────────
_WINPAY = 1.0 / 0.577 - 1.0  # per-leg payout at the 2-pick breakeven (57.7%)


def _perf_verdict(lo: float, hi: float, n: int) -> str:
    """Honest label for a rate given its Wilson 95% CI (fractions) vs breakeven."""
    if n == 0:
        return "—"
    if lo >= BREAKEVEN:
        return "edge"                      # even the CI floor clears breakeven
    if hi < BREAKEVEN:
        return "below breakeven"           # even the CI ceiling misses it
    return "not proven"                    # CI straddles breakeven


def fetch_performance() -> dict:
    """Track record from SETTLED picks (paper / hypothetical), measured HONESTLY.

    The recommended-tier rate is NOT the old in-sample headline. It is the audited
    number:
      • forward-only — picks logged at/after game start (lookahead) are excluded
        at the source, exactly as honest_oos does;
      • point-in-time — the tier is selected by walk_forward_oos, where every pick
        is judged by a cutoff table fit ONLY on strictly-prior settlements, so no
        cutoff ever sees the window it is scored on.
    Every rate ships with its Wilson 95% CI and an honest verdict; nothing here is
    presented as a proven edge unless the CI floor actually clears breakeven.
    """
    if not _HONEST_OK:
        raise RuntimeError("honest_oos harness unavailable — refusing to serve an "
                           "in-sample track record")
    from collections import defaultdict

    with engine.connect() as conn:
        picks = _load_gated_settled(conn)   # forward-only + valid-line, shared loader

    # The honest recommended tier: point-in-time walk-forward selection.
    recs = walk_forward_oos(picks)

    def summ(rs: list[dict]) -> dict:
        w = sum(r["win"] for r in rs)
        n = len(rs)
        lo, hi = wilson_ci(w, n)
        return {"pct": round(w / n * 100, 1) if n else 0.0, "w": w, "l": n - w, "n": n,
                "lo": round(lo * 100, 1), "hi": round(hi * 100, 1),
                "verdict": _perf_verdict(lo, hi, n)}

    rec = summ(recs)
    allp = summ(picks)

    # CLV: did our side's no-vig prob improve from open to close? (all fwd picks)
    clv_vals = []
    for r in picks:
        mo, mc = r["market_prob"], r["market_prob_close"]
        if mo is None or mc is None:
            continue
        over = r["direction"] == "over"
        so = float(mo) if over else 1 - float(mo)
        sc = float(mc) if over else 1 - float(mc)
        clv_vals.append(sc - so)
    clv = round(sum(clv_vals) / len(clv_vals) * 100, 1) if clv_vals else 0.0

    # rolling rec-tier win-rate trend (~14 pts, trailing window) over honest recs.
    recs_sorted = sorted((r for r in recs if r["settled"] is not None), key=lambda r: r["settled"])
    trend = []
    if len(recs_sorted) >= 10:
        win = max(20, len(recs_sorted) // 8)
        pts = 14
        for i in range(pts):
            end = round((i + 1) / pts * len(recs_sorted))
            window = recs_sorted[max(0, end - win):end]
            if len(window) >= 5:
                w = sum(r["win"] for r in window)
                trend.append({"i": i, "pct": round(w / len(window) * 100, 1)})

    # by sport (honest rec tier) + paper ROI (flat 1u at breakeven odds), with CIs.
    bysport_map = defaultdict(list)
    for r in recs:
        bysport_map[r["sport"]].append(r)
    by_sport, roi_by_sport = [], []
    for sp, rs in sorted(bysport_map.items(), key=lambda kv: -len(kv[1])):
        s = summ(rs)
        if s["n"] < 5:
            continue
        by_sport.append({"sport": league_label(sp), "w": s["w"], "l": s["l"],
                         "pct": s["pct"], "lo": s["lo"], "hi": s["hi"], "verdict": s["verdict"]})
        roi = (s["w"] * _WINPAY - s["l"]) / s["n"] * 100
        roi_by_sport.append({"sport": league_label(sp), "roi": round(roi, 1)})

    # calibration bins + Brier over all forward-only picks (model prob vs outcome).
    bins = [(0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 1.01)]
    calib = []
    for lo, hi in bins:
        b = [r for r in picks if lo <= r["model_prob"] < hi]
        if len(b) < 10:
            continue
        pred = sum(r["model_prob"] for r in b) / len(b)
        act = sum(r["win"] for r in b) / len(b)
        calib.append({"pred": round(pred * 100, 1), "actual": round(act * 100, 1), "n": len(b)})
    brier = round(sum((r["model_prob"] - r["win"]) ** 2 for r in picks) / len(picks), 3) if picks else None

    # by market × lean (honest rec tier), each with its CI. Sorted by SAMPLE SIZE,
    # never by win rate — so no lucky single bucket floats to the top as a headline
    # (that framing is exactly how the killed mlb|hits|under mirage was surfaced).
    mk = defaultdict(list)
    for r in recs:
        mk[(r["stat_type"], r["direction"])].append(r)
    by_market = []
    for (stat, direction), rs in mk.items():
        s = summ(rs)
        if s["n"] >= 8:
            by_market.append({"market": stat_label(stat), "lean": direction,
                              "pct": s["pct"], "n": s["n"], "lo": s["lo"], "hi": s["hi"]})
    by_market.sort(key=lambda x: -x["n"])

    return {
        "recommended": rec,
        "all_picks": {"pct": allp["pct"], "w": allp["w"], "l": allp["l"]},
        "clv_pct": clv,
        "breakeven": round(BREAKEVEN * 100, 1),
        "method": "point-in-time walk-forward · forward-only + valid-line-only",
        "trend": trend,
        "by_sport": by_sport,
        "roi_by_sport": roi_by_sport,
        "calibration": calib,
        "brier": brier,
        "by_market": by_market[:8],
    }


def fetch_soft_lines(league=None) -> list[dict]:
    """Market-based +EV signals (independent of the model): PrizePicks lines the
    sharp no-vig consensus prices as beatable. Latest run, meaningful edge only."""
    params = {}
    lf = ""
    if league:
        lf = "AND sport_code = :league"
        params["league"] = league.lower()
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT sport_code, player_name, stat_type, pp_line, sharp_line,
                   sharp_over_prob, best_side, best_prob, edge
            FROM soft_lines
            WHERE run_date = (SELECT MAX(run_date) FROM soft_lines)
              AND best_prob >= 0.55 {lf}
            ORDER BY best_prob DESC
            LIMIT 60
        """), params).mappings().all()

    out = []
    for r in rows:
        best = float(r["best_prob"])
        over = r["best_side"] == "over"
        sharp_over = float(r["sharp_over_prob"]) if r["sharp_over_prob"] is not None else 0.5
        out.append({
            "player": {"name": r["player_name"], "team": "", "headshot_url": None},
            "league": r["sport_code"],
            "stat_type": stat_label(r["stat_type"]),
            "pp_line": float(r["pp_line"]),
            "sharp_line": float(r["sharp_line"]) if r["sharp_line"] is not None else None,
            "recommendation": "over" if over else "under",
            "market_ev_pct": round((best - 0.5) / 0.5 * 100 * 0.5, 1),  # EV over a coinflip stake
            "consensus_prob": round(best * 100),
            "sharp_over_prob": round(sharp_over, 3),
        })
    return out
