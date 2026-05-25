"""Scrape current PrizePicks projections and land them in prop_lines."""
import json
from datetime import datetime, timezone
from curl_cffi import requests as cc_requests
from sqlalchemy import text
from tenacity import retry, stop_after_attempt, wait_exponential
from props.utils.db import session_scope
from props.utils.logging import log, configure_logging

PRIZEPICKS_URL = "https://api.prizepicks.com/projections"

# Map PrizePicks league IDs to our sport_code values.
# Add or adjust here as more leagues become available.
LEAGUE_TO_SPORT = {
    "2": "mlb",
    "7": "nba",
    "8": "nhl",
    "3": "wnba",
    # "163": "nfl",  # NFLSZN — season-long, enable when game props arrive
}

# Map PrizePicks stat_type names to our canonical stat_type strings.
# Keep this conservative — only stats we plan to model.
STAT_TYPE_MAP = {
    # MLB
    "Hits": "hits",
    "Total Bases": "total_bases",
    "RBIs": "rbis",
    "Runs": "runs",
    "Singles": "singles",
    "Hitter Strikeouts": "strikeouts_batter",
    "Walks": "walks",
    "Hits+Runs+RBIs": "hits_runs_rbis",
    "Pitcher Strikeouts": "strikeouts_pitcher",
    "Pitching Outs": "pitching_outs",
    "Earned Runs Allowed": "earned_runs_allowed",
    # NBA / WNBA
    "Points": "points",
    "Rebounds": "rebounds",
    "Assists": "assists",
    "3-PT Made": "threes_made",
    "Pts+Rebs+Asts": "pra",
    "Pts+Rebs": "pts_rebs",
    "Pts+Asts": "pts_asts",
    "Rebs+Asts": "rebs_asts",
    "Blocked Shots": "blocks",
    "Steals": "steals",
    "Fantasy Score": "fantasy_score",
    # NHL
    "Shots On Goal": "shots_on_goal",
    "Shots": "shots",
    "Goals": "goals",
    "Saves": "saves",
    "Goalie Saves": "saves",
}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
def fetch_projections() -> dict:
    r = cc_requests.get(
        PRIZEPICKS_URL,
        params={"per_page": 10000, "single_stat": "true"},
        impersonate="chrome120",
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def build_included_lookup(included: list) -> dict:
    """Convert included array into {(type, id): attributes} for fast joins."""
    lookup = {}
    for item in included:
        key = (item["type"], item["id"])
        lookup[key] = item.get("attributes", {})
    return lookup


def parse_projection(proj: dict, included_lookup: dict) -> dict | None:
    """Extract a flat record from one projection. Returns None if we should skip."""
    attrs = proj.get("attributes", {})
    rels = proj.get("relationships", {})

    # Resolve league
    league_id = rels.get("league", {}).get("data", {}).get("id")
    sport_code = LEAGUE_TO_SPORT.get(league_id)
    if not sport_code:
        return None  # Not a sport we care about

    # Resolve stat type
    stat_display = attrs.get("stat_display_name") or attrs.get("stat_type")
    canonical_stat = STAT_TYPE_MAP.get(stat_display)
    if not canonical_stat:
        return None  # Stat we don't model

    # Resolve player
    player_rel = rels.get("new_player", {}).get("data", {})
    if not player_rel:
        return None
    player_attrs = included_lookup.get(("new_player", player_rel["id"]), {})
    player_external_id = player_rel["id"]
    player_name = player_attrs.get("display_name") or player_attrs.get("name", "")
    team_abbr = player_attrs.get("team")

    # Resolve game
    game_rel = rels.get("game", {}).get("data") or {}
    game_external_id = game_rel.get("id") if game_rel else None

    return {
        "projection_id": proj["id"],
        "sport_code": sport_code,
        "player_external_id": player_external_id,
        "player_name": player_name,
        "team_abbr": team_abbr,
        "game_external_id": game_external_id,
        "stat_type": canonical_stat,
        "stat_display": stat_display,
        "line_value": float(attrs["line_score"]),
        "start_time": attrs.get("start_time"),
        "is_promo": attrs.get("is_promo", False),
        "is_live": attrs.get("is_live", False),
    }


def find_or_create_player(session, sport_code, external_id, name, team_abbr) -> int | None:
    """Look up player by PrizePicks external_id. If not found, try name match. Else create."""
    # PrizePicks uses different external IDs than the MLB API.
    # We'll store the PP id with a 'pp_' prefix to avoid colliding with our existing IDs.
    pp_id = f"pp_{external_id}"

    result = session.execute(
        text("SELECT player_id FROM players WHERE sport_code=:sc AND external_id=:eid"),
        {"sc": sport_code, "eid": pp_id},
    ).first()
    if result:
        return result[0]

    # Try fuzzy name match against existing players (uses pg_trgm index)
    result = session.execute(
        text("""
            SELECT player_id FROM players
            WHERE sport_code = :sc
              AND similarity(full_name, :name) > 0.8
            ORDER BY similarity(full_name, :name) DESC
            LIMIT 1
        """),
        {"sc": sport_code, "name": name},
    ).first()
    if result:
        # Link the PrizePicks ID to this player by inserting a second row?
        # Simpler: just remember the mapping by creating a new player row with pp_ id.
        # For now we just return the matched player. We'll lose the explicit pp link.
        # TODO: consider a separate player_aliases table if collisions get bad.
        return result[0]

    # Create new
    result = session.execute(
        text("""
            INSERT INTO players (sport_code, external_id, full_name)
            VALUES (:sc, :eid, :name)
            RETURNING player_id
        """),
        {"sc": sport_code, "eid": pp_id, "name": name},
    ).first()
    return result[0]


def find_game(session, sport_code, external_id) -> int | None:
    """Best-effort game match. PrizePicks game IDs don't match MLB Stats API IDs,
    so for now we'll just store NULL and rely on player+start_time for joins later.
    """
    return None  # We'll resolve game linkage in a separate pass; not blocking here.


def run():
    configure_logging()
    started = datetime.now(timezone.utc)

    with session_scope() as session:
        run_id = session.execute(text("""
            INSERT INTO ingestion_runs (source, started_at, status)
            VALUES ('prizepicks_projections', :s, 'running')
            RETURNING run_id
        """), {"s": started}).scalar()

    try:
        payload = fetch_projections()
    except Exception as e:
        log.error("fetch_failed", error=str(e))
        with session_scope() as session:
            session.execute(text("""
                UPDATE ingestion_runs SET completed_at=NOW(), status='failed',
                    error_message=:em WHERE run_id=:rid
            """), {"em": str(e), "rid": run_id})
        return

    included_lookup = build_included_lookup(payload.get("included", []))
    projections = payload.get("data", [])
    log.info("fetched_projections", total=len(projections))

    snapshot_at = datetime.now(timezone.utc)
    inserted = 0
    skipped = 0

    with session_scope() as session:
        for proj in projections:
            try:
                rec = parse_projection(proj, included_lookup)
            except Exception as e:
                log.error("parse_error", proj_id=proj.get("id"), error=str(e))
                skipped += 1
                continue
            if rec is None:
                skipped += 1
                continue

            # Skip promos and live props for now — they have different pricing dynamics
            if rec["is_promo"] or rec["is_live"]:
                skipped += 1
                continue

            player_id = find_or_create_player(
                session, rec["sport_code"], rec["player_external_id"],
                rec["player_name"], rec["team_abbr"],
            )
            if player_id is None:
                skipped += 1
                continue

            # game_id resolution: we punt for now (NULL not allowed by schema, so we'd
            # need to either fix the schema or resolve here). Let's check.
            # The schema has game_id NOT NULL. We need a game row. Two options:
            # 1) Resolve to our existing game row (requires ID mapping)
            # 2) Insert a placeholder game row keyed by PP game_id
            # Going with option 2 for now: create a placeholder game.
            game_id = ensure_pp_game(
                session, rec["sport_code"], rec["game_external_id"], rec["start_time"]
            )
            if game_id is None:
                skipped += 1
                continue

            session.execute(text("""
                INSERT INTO prop_lines (
                    sportsbook, sport_code, player_id, game_id, stat_type,
                    line_value, is_pickem, snapshot_at
                )
                VALUES ('prizepicks', :sc, :pid, :gid, :stat, :line, TRUE, :ts)
            """), {
                "sc": rec["sport_code"], "pid": player_id, "gid": game_id,
                "stat": rec["stat_type"], "line": rec["line_value"], "ts": snapshot_at,
            })
            inserted += 1

    with session_scope() as session:
        session.execute(text("""
            UPDATE ingestion_runs SET completed_at=NOW(),
                rows_inserted=:n, status='success' WHERE run_id=:rid
        """), {"n": inserted, "rid": run_id})

    log.info("prizepicks_scrape_complete", inserted=inserted, skipped=skipped,
             total=len(projections))


def ensure_pp_game(session, sport_code, pp_game_id, start_time) -> int | None:
    """Create or find a placeholder game row keyed by PrizePicks game_id."""
    if not pp_game_id:
        return None
    ext_id = f"pp_{pp_game_id}"
    result = session.execute(
        text("SELECT game_id FROM games WHERE sport_code=:sc AND external_id=:eid"),
        {"sc": sport_code, "eid": ext_id},
    ).first()
    if result:
        return result[0]

    # Need home/away team IDs but PP doesn't give them cleanly in the projection.
    # Create a placeholder using team_id=1 for both — we'll resolve real teams later.
    # First make sure team_id=1 exists for this sport.
    placeholder_team = session.execute(
        text("""
            SELECT team_id FROM teams WHERE sport_code=:sc
            ORDER BY team_id LIMIT 1
        """), {"sc": sport_code},
    ).first()
    if not placeholder_team:
        # No teams for this sport yet — create a dummy
        placeholder_team = session.execute(text("""
            INSERT INTO teams (sport_code, external_id, abbreviation, name)
            VALUES (:sc, 'PP_PLACEHOLDER', 'PPL', 'PrizePicks Placeholder')
            RETURNING team_id
        """), {"sc": sport_code}).first()
    tid = placeholder_team[0]

    try:
        game_date_str = start_time[:10] if start_time else datetime.now().date().isoformat()
        season = game_date_str[:4]
    except Exception:
        game_date_str = datetime.now().date().isoformat()
        season = datetime.now().year

    result = session.execute(text("""
        INSERT INTO games (sport_code, external_id, game_date, game_datetime,
                           season, season_type, home_team_id, away_team_id, status)
        VALUES (:sc, :eid, :gd, :gdt, :season, 'regular', :t, :t, 'scheduled')
        RETURNING game_id
    """), {
        "sc": sport_code, "eid": ext_id, "gd": game_date_str,
        "gdt": start_time, "season": str(season), "t": tid,
    }).first()
    return result[0]


if __name__ == "__main__":
    run()
