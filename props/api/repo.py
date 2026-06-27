"""Read model picks from Postgres and shape them into the pick-board contract.

Mirrors the Streamlit dashboard's computations field-for-field so the board shows
the SAME numbers the rest of prop-edge does — confidence (calibrated prob), likely
range (interval calibration), paper Kelly (expected_value), recent form (graded
actuals), weather, insight, the diversified Top-Slate + paper slate-Kelly stakes,
and game predictions. No model math is re-derived here; this only reads + formats.

RESEARCH / PAPER-TRACKING: money sizing is hypothetical, never betting advice.
"""
from __future__ import annotations

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
        max_stake = round(MAX_STAKE_PER_PICK * 100)
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
