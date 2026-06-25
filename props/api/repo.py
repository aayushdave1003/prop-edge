"""Read model picks from Postgres and shape them into the /api/picks contract.

Mirrors the Streamlit dashboard's `load_todays_picks` joins so the board shows the
SAME numbers the rest of prop-edge does. No model math happens here — projection,
line, direction and probability are read straight from the pipeline's output;
`edge_pct` is just the projection-vs-line gap and `confidence` is a tier off the
model probability vs the per-category recommend cutoff.
"""
from __future__ import annotations

from sqlalchemy import text

from props.utils.db import engine
from props.api.formatting import stat_label, league_label

# --- per-category recommend cutoffs (for the confidence tier) --------------
# Loaded once; the DB-computed table is best, the committed seed JSON is the
# fallback, and a flat constant is the last resort so the API never hard-fails.
try:  # pragma: no cover - exercised at import
    from props.models.category_cutoffs import rec_cutoff, load_cutoffs, compute_from_db

    try:
        _CUTOFFS = compute_from_db(engine)
    except Exception:
        _CUTOFFS = load_cutoffs()
except Exception:  # category_cutoffs unavailable
    rec_cutoff = None
    _CUTOFFS = {}


def _confidence(sport_code: str, stat_type: str, direction: str, model_prob: float) -> str:
    """3-tier confidence from how far the model probability clears the category's
    recommend cutoff (NBA needs a higher prob than MLB to be a real edge)."""
    if model_prob is None:
        return "low"
    cut = 0.57
    if rec_cutoff is not None:
        try:
            cut = float(rec_cutoff(sport_code, stat_type, table=_CUTOFFS, direction=direction))
        except Exception:
            cut = 0.57
    margin = float(model_prob) - cut
    if margin >= 0.06:
        return "high"
    if margin >= 0.0:
        return "med"
    return "low"


_PICKS_SQL = """
    SELECT
        pk.pick_id,
        g.sport_code,
        p.full_name        AS player,
        p.photo_url        AS photo_url,
        t.abbreviation     AS team,
        pk.stat_type,
        pl.line_value      AS line,
        pk.direction,
        pk.model_prob      AS model_prob,
        pr.predicted_mean  AS predicted_mean,
        g.game_datetime    AS game_datetime,
        ht.abbreviation    AS home_team,
        at.abbreviation    AS away_team
    FROM picks pk
    JOIN players p     USING (player_id)
    LEFT JOIN teams t  ON t.team_id = p.current_team_id
    JOIN games   g     USING (game_id)
    JOIN prop_lines pl ON pl.line_id = pk.line_id
    LEFT JOIN teams ht ON ht.team_id = g.home_team_id
    LEFT JOIN teams at ON at.team_id = g.away_team_id
    LEFT JOIN predictions pr ON pr.prediction_id = pk.prediction_id
    WHERE (pk.picked_at AT TIME ZONE 'America/Los_Angeles')::date
          = (NOW() AT TIME ZONE 'America/Los_Angeles')::date
      AND g.game_date = (NOW() AT TIME ZONE 'America/Los_Angeles')::date
      AND (pk.leg_result IS NULL OR pk.leg_result != 'void')
      AND pr.predicted_mean IS NOT NULL
      AND pl.line_value IS NOT NULL
      {league_filter}
      {stat_filter}
    ORDER BY COALESCE(pk.market_edge, pk.edge) DESC
"""


def fetch_picks(league: str | None = None, stat: str | None = None) -> list[dict]:
    """Return today's picks as contract dicts, optionally filtered by league/stat."""
    params: dict = {}
    league_filter = ""
    stat_filter = ""
    if league:
        league_filter = "AND g.sport_code = :league"
        params["league"] = league.lower()
    if stat:
        stat_filter = "AND pk.stat_type = :stat"
        params["stat"] = stat
    sql = _PICKS_SQL.format(league_filter=league_filter, stat_filter=stat_filter)

    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()

    out: list[dict] = []
    for r in rows:
        line = float(r["line"])
        proj = float(r["predicted_mean"])
        edge_pct = round((proj - line) / line * 100, 1) if line else 0.0
        direction = r["direction"]
        recommendation = "more" if direction == "over" else "less"
        model_prob = float(r["model_prob"]) if r["model_prob"] is not None else None
        start = r["game_datetime"]
        out.append({
            "id": str(r["pick_id"]),
            "league": r["sport_code"],
            "player": {
                "name": r["player"],
                "team": r["team"] or "",
                "headshot_url": r["photo_url"] or None,
            },
            "matchup": f"{r['away_team'] or '?'} @ {r['home_team'] or '?'}",
            "start_time": start.isoformat() if start is not None else None,
            "stat_type": stat_label(r["stat_type"]),
            "stat_key": r["stat_type"],
            "pp_line": round(line, 2),
            "model_projection": round(proj, 2),
            "edge_pct": edge_pct,
            "recommendation": recommendation,
            "confidence": _confidence(r["sport_code"], r["stat_type"], direction, model_prob),
        })
    return out


_LEAGUES_SQL = """
    SELECT g.sport_code, pk.stat_type, COUNT(*) AS n
    FROM picks pk
    JOIN games g USING (game_id)
    JOIN prop_lines pl ON pl.line_id = pk.line_id
    LEFT JOIN predictions pr ON pr.prediction_id = pk.prediction_id
    WHERE (pk.picked_at AT TIME ZONE 'America/Los_Angeles')::date
          = (NOW() AT TIME ZONE 'America/Los_Angeles')::date
      AND g.game_date = (NOW() AT TIME ZONE 'America/Los_Angeles')::date
      AND (pk.leg_result IS NULL OR pk.leg_result != 'void')
      AND pr.predicted_mean IS NOT NULL
      AND pl.line_value IS NOT NULL
    GROUP BY g.sport_code, pk.stat_type
    ORDER BY g.sport_code, n DESC
"""


def fetch_leagues() -> list[dict]:
    """Leagues that have picks today + their available stat types, for the filter
    rows. Lets the UI add leagues with zero frontend changes."""
    with engine.connect() as conn:
        rows = conn.execute(text(_LEAGUES_SQL)).mappings().all()

    by_league: dict[str, dict] = {}
    for r in rows:
        sc = r["sport_code"]
        lg = by_league.setdefault(sc, {
            "code": sc, "label": league_label(sc), "count": 0, "stats": [],
        })
        lg["count"] += int(r["n"])
        lg["stats"].append({
            "key": r["stat_type"],
            "label": stat_label(r["stat_type"]),
            "count": int(r["n"]),
        })
    # most picks first
    return sorted(by_league.values(), key=lambda x: x["count"], reverse=True)
