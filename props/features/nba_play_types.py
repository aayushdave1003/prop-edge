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
import json
import time
import pandas as pd
from sqlalchemy import text
from nba_api.stats.endpoints import synergyplaytypes
from props.utils.db import engine, session_scope
from props.utils.logging import log, configure_logging
from props.features.derived_writer import write_derived

PLAY_TYPES = ["Isolation", "PRBallHandler", "SpotUp", "PostUp", "Cut"]
SEASONS    = ["2024-25", "2025-26"]


def fetch_play_type(play_type: str, player_or_team: str,
                    season: str, season_type: str) -> pd.DataFrame:
    try:
        ep = synergyplaytypes.SynergyPlayTypes(
            league_id="00",
            per_mode_simple="PerGame",
            player_or_team_abbreviation=player_or_team,
            season=season,
            season_type_all_star=season_type,
            play_type_nullable=play_type,
            type_grouping_nullable="offensive",
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
        return pd.DataFrame()


def build_team_play_type_map() -> dict:
    """Returns {team_id_ext: {iso_rate, pnr_bh_rate, spotup_rate}} per season."""
    records = {}
    for season in SEASONS:
        for stype in ["Regular Season", "Playoffs"]:
            for pt in ["Isolation", "PRBallHandler", "SpotUp"]:
                df = fetch_play_type(pt, "T", season, stype)
                if df.empty:
                    continue
                for _, row in df.iterrows():
                    tid = str(row.get("TEAM_ID", ""))
                    key = (tid, season, stype)
                    if key not in records:
                        records[key] = {}
                    col = {
                        "Isolation":   "team_isolation_rate",
                        "PRBallHandler": "team_pnr_bh_rate",
                        "SpotUp":      "team_spotup_rate",
                    }[pt]
                    records[key][col] = round(float(row.get("POSS_PCT", 0)), 4)
    return records


def build_player_play_type_map() -> dict:
    """Returns {player_id_ext: {iso_pct, pnr_bh_pct, spotup_pct}} per season."""
    records = {}
    for season in SEASONS:
        for stype in ["Regular Season", "Playoffs"]:
            for pt in ["Isolation", "PRBallHandler", "SpotUp"]:
                df = fetch_play_type(pt, "P", season, stype)
                if df.empty:
                    continue
                for _, row in df.iterrows():
                    pid = str(row.get("PLAYER_ID", ""))
                    key = (pid, season, stype)
                    if key not in records:
                        records[key] = {}
                    col = {
                        "Isolation":   "player_isolation_pct",
                        "PRBallHandler": "player_pnr_bh_pct",
                        "SpotUp":      "player_spotup_pct",
                    }[pt]
                    records[key][col] = round(float(row.get("POSS_PCT", 0)), 4)
    return records


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
