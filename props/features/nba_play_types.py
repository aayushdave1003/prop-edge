"""Fetch team and player play-type frequencies from NBA Stats API.

Pulls synergy play-type data (isolation, PnR ball-handler, PnR roll man,
spot-up, post-up, cut) for both regular season and playoffs.

Stores in player_games.derived as:
  team_isolation_rate      — % of team possessions that are isos
  team_pnr_bh_rate         — % that are pick-and-roll (ball handler)
  team_spotup_rate         — % that are spot-up catch-and-shoot
  player_isolation_pct     — % of THIS player's possessions that are isos
  player_pnr_bh_pct        — % of THIS player's possessions that are P&R BH
  player_spotup_pct        — % catch-and-shoot

These tell the model:
  - High isolation team = star-driven, fewer passing lanes
  - High PnR BH = ball handler racks up AST + PTS
  - High spot-up player = off-ball scorer, depends on creation from others
"""
import time
import pandas as pd
from nba_api.stats.endpoints import synergyplaytypes
from props.utils.db import engine
from props.utils.logging import log, configure_logging
from props.features.derived_writer import write_derived

PLAY_TYPES = ["Isolation", "PRBallHandler", "SpotUp", "PostUp", "Cut"]
SEASONS    = ["2024-25", "2025-26"]
NBA_TIMEOUT = 20        # per-call cap (nba_api defaults to 30s — too long when stats.nba.com is down)
MAX_CONSEC_FAILS = 3    # stats.nba.com unresponsive → stop grinding through ~20s timeouts on every combo

TEAM_COLS = {"Isolation": "team_isolation_rate", "PRBallHandler": "team_pnr_bh_rate",
             "SpotUp": "team_spotup_rate"}
PLAYER_COLS = {"Isolation": "player_isolation_pct", "PRBallHandler": "player_pnr_bh_pct",
               "SpotUp": "player_spotup_pct"}


def fetch_play_type(play_type: str, player_or_team: str,
                    season: str, season_type: str):
    """DataFrame on success (may be empty if no data), or None on fetch failure."""
    try:
        ep = synergyplaytypes.SynergyPlayTypes(
            league_id="00",
            per_mode_simple="PerGame",
            player_or_team_abbreviation=player_or_team,
            season=season,
            season_type_all_star=season_type,
            play_type_nullable=play_type,
            type_grouping_nullable="offensive",
            timeout=NBA_TIMEOUT,
        )
        df = ep.get_data_frames()[0]
        df["play_type"]   = play_type
        df["season"]      = season
        df["season_type"] = season_type
        time.sleep(0.6)
        return df
    except Exception as e:
        log.warning("play_type_fetch_failed", play_type=play_type,
                    season=season, err=str(e))
        return None


def _build_play_type_map(player_or_team: str, id_col: str, col_map: dict) -> dict:
    """Shared team/player builder. Circuit breaker: after MAX_CONSEC_FAILS fetches
    fail in a row, stats.nba.com is down — bail out with whatever we have instead of
    burning a ~20s timeout on every remaining combo (a flaky NBA-API day otherwise
    dragged the daily run out by ~15 min). Partial/empty is fine; the next healthy
    run backfills these supplementary features."""
    records: dict = {}
    consec_fail = 0
    for season in SEASONS:
        for stype in ["Regular Season", "Playoffs"]:
            for pt in ["Isolation", "PRBallHandler", "SpotUp"]:
                df = fetch_play_type(pt, player_or_team, season, stype)
                if df is None:
                    consec_fail += 1
                    if consec_fail >= MAX_CONSEC_FAILS:
                        log.warning("play_type_circuit_open",
                                    detail="stats.nba.com unresponsive; skipping remaining play-type fetches",
                                    collected=len(records))
                        return records
                    continue
                consec_fail = 0
                for _, row in df.iterrows():
                    key = (str(row.get(id_col, "")), season, stype)
                    records.setdefault(key, {})[col_map[pt]] = round(float(row.get("POSS_PCT", 0)), 4)
    return records


def build_team_play_type_map() -> dict:
    """Returns {(team_id_ext, season, stype): {iso_rate, pnr_bh_rate, spotup_rate}}."""
    return _build_play_type_map("T", "TEAM_ID", TEAM_COLS)


def build_player_play_type_map() -> dict:
    """Returns {(player_id_ext, season, stype): {iso_pct, pnr_bh_pct, spotup_pct}}."""
    return _build_play_type_map("P", "PLAYER_ID", PLAYER_COLS)


def apply_to_derived(team_map: dict, player_map: dict):
    """Write play-type features into player_games.derived."""
    log.info("applying_play_type_features_to_derived")

    # Load all NBA player_games with external IDs
    rows = pd.read_sql("""
        SELECT pg.player_game_id, pg.team_id, pg.player_id,
               t.external_id AS team_ext, p.external_id AS player_ext,
               g.season, g.season_type
        FROM player_games pg
        JOIN games g ON g.game_id = pg.game_id
        JOIN teams t ON t.team_id = pg.team_id
        JOIN players p ON p.player_id = pg.player_id
        WHERE g.sport_code = 'nba'
    """, engine)

    def _stype_key(stype: str) -> str:
        if stype in ("playoffs", "play_in"):
            return "Playoffs"
        return "Regular Season"

    items = []
    for _, row in rows.iterrows():
        season  = f"20{str(row['season'])[-2:]}-{int(str(row['season'])[-2:])+1:02d}" \
                  if len(str(row['season'])) == 4 else row['season']
        stype   = _stype_key(row.get("season_type", ""))

        patch = {}
        patch.update(team_map.get((str(row["team_ext"]), season, stype), {}))
        patch.update(player_map.get((str(row["player_ext"]), season, stype), {}))

        if patch:
            items.append((int(row["player_game_id"]), patch))

    updated = write_derived(items, mode="merge", label="nba_play_types")
    log.info("play_type_features_applied", updated=updated)


def run():
    configure_logging()
    log.info("fetching_play_type_data")
    team_map   = build_team_play_type_map()
    player_map = build_player_play_type_map()
    log.info("play_type_data_fetched",
             team_records=len(team_map), player_records=len(player_map))
    apply_to_derived(team_map, player_map)
    log.info("nba_play_types_complete")


if __name__ == "__main__":
    run()
