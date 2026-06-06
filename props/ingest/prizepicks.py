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

# PrizePicks team abbreviations that differ from ours, per sport. NBA/NHL mostly
# match already; MLB abbreviations are ambiguous on both sides so we don't alias
# them — find_real_game falls back safely when a match isn't unambiguous.
PP_ABBR_ALIAS = {
    "wnba": {"LAS": "LA", "NYL": "NY", "PDX": "POR", "WAS": "WSH",
             "CONN": "CON", "LVA": "LV", "GSV": "GS"},
}

# Map PrizePicks stat_type names to our canonical stat_type strings.
# Keep this conservative — only stats we plan to model.
STAT_TYPE_MAP = {
    # MLB
    "Hits": "hits",
    "TB": "total_bases",
    "Total Bases": "total_bases",
    "RBIs": "rbis",
    "Runs": "runs",
    "Singles": "singles",
    "Doubles": "doubles",
    "Triples": "triples",
    "Home Runs": "home_runs",
    "SB": "stolen_bases",
    "Hitter Ks": "strikeouts_batter",
    "Hitter Strikeouts": "strikeouts_batter",
    "Walks": "walks",
    "Hits+Runs+RBIs": "hits_runs_rbis",
    "Hitter FS": "fantasy_score_batter",
    "Ks": "strikeouts_pitcher",
    "Pitcher Strikeouts": "strikeouts_pitcher",
    "Pitcher FS": "fantasy_score_pitcher",
    "PO": "pitching_outs",
    "Pitching Outs": "pitching_outs",
    "Pitches Thrown": "pitches_thrown",
    "Earned Runs Allowed": "earned_runs_allowed",
    "Walks Allowed": "walks_allowed",
    "Hits Allowed": "hits_allowed",
    # NBA / WNBA
    "Points": "points",
    "Rebounds": "rebounds",
    "Assists": "assists",
    "3-PT Made": "threes_made",
    "3PTM": "threes_made",
    "3-PT Attempted": "threes_attempted",
    "3PTA": "threes_attempted",
    "Pts+Rebs+Asts": "pts_rebs_asts",
    "PRA": "pts_rebs_asts",
    "Pts+Rebs": "pts_rebs",
    "Pts+Asts": "pts_asts",
    "Rebs+Asts": "rebs_asts",
    "Blocked Shots": "blocks",
    "Steals": "steals",
    "Blks+Stls": "blocks_steals",
    "Turnovers": "turnovers",
    "Personal Fouls": "personal_fouls",
    "FG Made": "fg_made",
    "FG Attempted": "fg_attempted",
    "2-PT Made": "two_made",
    "2-PT Att": "two_attempted",
    "FTM": "ft_made",
    "FTA": "ft_attempted",
    "Defensive Rebounds": "def_rebounds",
    "Offensive Rebounds": "off_rebounds",
    "Fantasy Score": "fantasy_score",
    "Dunks": "dunks",
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

    # Skip combined-player Power Play lines (e.g. "Caitlin Clark + Kelsey Mitchell")
    # These are multi-player props that look like single-player lines but aren't.
    if " + " in player_name:
        return None

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
        "odds_type": attrs.get("odds_type", "standard"),
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
        return result[0]

    # Last-name fallback for sports where box score sources use abbreviated first names
    # (e.g. NHL API returns "Z. Benson" while PrizePicks has "Zach Benson").
    # Match on the last word of the PrizePicks full name.
    last_name = name.rsplit(" ", 1)[-1]
    result = session.execute(
        text("""
            SELECT player_id FROM players
            WHERE sport_code = :sc
              AND external_id NOT LIKE 'pp_%%'
              AND full_name LIKE '%%' || :last
            LIMIT 1
        """),
        {"sc": sport_code, "last": last_name},
    ).first()
    if result:
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

            # Resolve to the player's REAL game (team + date) when we can; only
            # fall back to a pp_ placeholder when the match isn't unambiguous.
            game_id = find_real_game(
                session, rec["sport_code"], rec["team_abbr"], rec["start_time"]
            )
            if game_id is None:
                game_id = ensure_pp_game(
                    session, rec["sport_code"], rec["game_external_id"], rec["start_time"]
                )
            if game_id is None:
                skipped += 1
                continue

            session.execute(text("""
                INSERT INTO prop_lines (
                    sportsbook, sport_code, player_id, game_id, stat_type,
                    line_value, line_variant, is_pickem, snapshot_at
                )
                VALUES ('prizepicks', :sc, :pid, :gid, :stat, :line, :variant, TRUE, :ts)
            """), {
                "sc": rec["sport_code"], "pid": player_id, "gid": game_id,
                "stat": rec["stat_type"], "line": rec["line_value"],
                "variant": rec["odds_type"], "ts": snapshot_at,
            })
            inserted += 1

    with session_scope() as session:
        session.execute(text("""
            UPDATE ingestion_runs SET completed_at=NOW(),
                rows_inserted=:n, status='success' WHERE run_id=:rid
        """), {"n": inserted, "rid": run_id})

    log.info("prizepicks_scrape_complete", inserted=inserted, skipped=skipped,
             total=len(projections))


def find_real_game(session, sport_code, team_abbr, start_time) -> int | None:
    """Resolve a line to a REAL (non-placeholder) game by team + date.

    Conservative on purpose: only returns a game when the team resolves to
    exactly one team_id AND exactly one real game on that date involves it. So
    ambiguous abbreviations (e.g. MLB's truncated city codes) safely return None
    and fall back to the placeholder + the settle-time resolver. This is what
    lets WNBA/NBA/NHL picks attach to real matchups instead of 'PPL'.
    """
    if not team_abbr or not start_time:
        return None
    abbr = PP_ABBR_ALIAS.get(sport_code, {}).get(team_abbr.upper(), team_abbr.upper())
    try:
        game_date = datetime.fromisoformat(start_time).date()
    except (ValueError, TypeError):
        try:
            game_date = datetime.strptime(start_time[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None
    teams = session.execute(text(
        "SELECT team_id FROM teams WHERE sport_code=:s AND UPPER(abbreviation)=:a"
    ), {"s": sport_code, "a": abbr}).all()
    if len(teams) != 1:
        return None
    tid = teams[0][0]
    games = session.execute(text("""
        SELECT game_id FROM games
        WHERE sport_code=:s AND external_id NOT LIKE 'pp_%'
          AND game_date BETWEEN :d - INTERVAL '1 day' AND :d + INTERVAL '1 day'
          AND (home_team_id=:t OR away_team_id=:t)
    """), {"s": sport_code, "d": game_date, "t": tid}).all()
    return games[0][0] if len(games) == 1 else None


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
