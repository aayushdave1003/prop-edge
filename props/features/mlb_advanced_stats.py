"""MLB Advanced Stats — makes the model understand baseball.

Batter features:
  k_rate          — strikeout rate (K/AB) — plate discipline
  bb_rate         — walk rate (BB/AB) — patience / on-base skill
  iso_power       — isolated power (XBH-weighted / AB) — raw power
  slg_proxy       — slugging proxy (TB/AB) — overall hitting value
  hard_contact_rate — extra base hits / AB — quality of contact
  babip_proxy     — hits on balls in play — luck/skill in contact
  hr_rate         — HR / AB — home run tendency

Pitcher features:
  bb9             — walks per 9 IP — command
  hr9             — HR per 9 IP — vulnerability
  pitch_efficiency — outs per pitch — pitch economy / deep start ability
  command_rate    — K / (K + BB) — pure command metric
  quality_start_rate — % of starts with 6+ IP and <4 ER

Park factor (static):
  park_factor — run environment at home ballpark (1.0 = neutral)
  Coors Field = 1.12, Petco = 0.91, etc.

Platoon advantage:
  platoon_advantage — 1 if batter/pitcher opposite hand (batter advantage),
                      -1 if same hand (pitcher advantage), 0 if unknown
  (requires handedness backfill — gracefully handled)
"""
import json
from datetime import datetime
import numpy as np
import pandas as pd
from sqlalchemy import text
from props.utils.db import engine, session_scope
from props.utils.logging import log, configure_logging

WINDOWS = [5, 10, 20]

# 2024-25 park factors (runs scored, relative to 1.0 neutral)
# Source: FanGraphs/Baseball-Reference park factors
PARK_FACTORS = {
    # Team name substring → park factor
    "Rockies":      1.12,  # Coors Field — extreme hitter park
    "Red Sox":      1.07,  # Fenway
    "Yankees":      1.05,  # Yankee Stadium
    "Cubs":         1.04,  # Wrigley Field
    "Brewers":      1.03,
    "Phillies":     1.03,
    "Reds":         1.02,
    "Rangers":      1.02,
    "Dodgers":      0.99,
    "Mets":         0.99,
    "Cardinals":    0.99,
    "Braves":       0.98,
    "Astros":       0.98,
    "Giants":       0.97,  # Oracle Park — pitcher friendly
    "Athletics":    0.97,
    "Angels":       0.97,
    "White Sox":    0.96,
    "Nationals":    0.96,
    "Tigers":       0.96,
    "Mariners":     0.95,
    "Rays":         0.95,
    "Blue Jays":    0.95,
    "Pirates":      0.95,
    "Guardians":    0.95,
    "Twins":        0.94,
    "Royals":       0.94,
    "Orioles":      0.94,
    "Marlins":      0.93,
    "Padres":       0.91,  # Petco Park — extreme pitcher park
    "Diamondbacks": 0.97,
}

DEFAULT_PARK_FACTOR = 0.98


def _park_factor_for_team(team_name: str) -> float:
    for k, v in PARK_FACTORS.items():
        if k.lower() in team_name.lower():
            return v
    return DEFAULT_PARK_FACTOR


def load_mlb_player_games() -> pd.DataFrame:
    log.info("loading_mlb_player_games_for_advanced_stats")
    df = pd.read_sql("""
        SELECT pg.player_game_id, pg.player_id, pg.game_id, pg.team_id,
               pg.opponent_id, pg.is_home, pg.stats,
               g.game_date, g.season, g.home_team_id,
               p.handedness, p.position,
               ht.name AS home_team_name
        FROM player_games pg
        JOIN games g USING (game_id)
        JOIN players p ON p.player_id = pg.player_id
        JOIN teams ht ON ht.team_id = g.home_team_id
        WHERE g.sport_code = 'mlb'
        ORDER BY pg.player_id, g.game_date, pg.player_game_id
    """, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    log.info("loaded", rows=len(df))
    return df


def explode_stats(df: pd.DataFrame) -> pd.DataFrame:
    stats = pd.json_normalize(df["stats"].tolist())
    out = df.drop(columns=["stats"]).reset_index(drop=True)
    batter_cols = ["hits", "at_bats", "walks", "strikeouts", "doubles",
                   "triples", "home_runs", "total_bases", "plate_appearances"]
    pitcher_cols = ["strikeouts_pitcher", "walks_allowed", "home_runs_allowed",
                    "outs_recorded", "pitches_thrown", "hits_allowed",
                    "batters_faced", "earned_runs"]
    for col in batter_cols + pitcher_cols:
        out[col] = pd.to_numeric(
            stats.get(col, pd.Series(0, index=stats.index)),
            errors="coerce").fillna(0)
    return out


def compute_batter_advanced(df: pd.DataFrame) -> pd.DataFrame:
    """Rate stats for batters: K%, BB%, ISO, hard contact, BABIP proxy."""
    log.info("computing_batter_advanced_stats")

    # Only batters (have at-bats)
    batters = df[df["at_bats"] > 0].copy()

    # Per-game computed stats
    batters["k_rate_raw"]          = batters["strikeouts"] / batters["at_bats"].replace(0, np.nan)
    batters["bb_rate_raw"]         = batters["walks"] / batters["plate_appearances"].replace(0, np.nan)
    batters["iso_raw"]             = (
        (batters["doubles"] + batters["triples"] * 2 + batters["home_runs"] * 3)
        / batters["at_bats"].replace(0, np.nan)
    )
    batters["slg_raw"]             = batters["total_bases"] / batters["at_bats"].replace(0, np.nan)
    batters["hard_contact_raw"]    = (
        (batters["doubles"] + batters["triples"] + batters["home_runs"])
        / batters["at_bats"].replace(0, np.nan)
    )
    batters["babip_raw"]           = (
        (batters["hits"] - batters["home_runs"])
        / (batters["at_bats"] - batters["strikeouts"] - batters["home_runs"]).replace(0, np.nan)
    )
    batters["hr_rate_raw"]         = batters["home_runs"] / batters["at_bats"].replace(0, np.nan)

    for col in ["k_rate_raw", "bb_rate_raw", "iso_raw", "slg_raw",
                "hard_contact_raw", "babip_raw", "hr_rate_raw"]:
        batters[col] = batters[col].fillna(0).clip(0, 1)

    results = []
    for pid, grp in batters.groupby("player_id", group_keys=False):
        g = grp.sort_values(["game_date", "player_game_id"]).reset_index(drop=True)
        feats = {"player_game_id": g["player_game_id"].values}
        season_marker = (g["season"] != g["season"].shift(1)).cumsum()

        for stat, col in [
            ("batter_k_rate",       "k_rate_raw"),
            ("batter_bb_rate",      "bb_rate_raw"),
            ("batter_iso",          "iso_raw"),
            ("batter_slg",          "slg_raw"),
            ("batter_hard_contact", "hard_contact_raw"),
            ("batter_babip",        "babip_raw"),
            ("batter_hr_rate",      "hr_rate_raw"),
        ]:
            pv = g[col].shift(1)
            for w in WINDOWS:
                feats[f"last_{w}_avg_{stat}"] = (
                    pv.rolling(w, min_periods=1).mean().fillna(0).round(4).values)
            feats[f"season_avg_{stat}"] = (
                g.groupby(season_marker)[col].apply(
                    lambda s: s.shift(1).expanding().mean()
                ).reset_index(level=0, drop=True).fillna(0).round(4).values)

        results.append(pd.DataFrame(feats))

    return pd.concat(results, ignore_index=True) if results else pd.DataFrame()


def compute_pitcher_advanced(df: pd.DataFrame) -> pd.DataFrame:
    """Advanced pitcher metrics: BB/9, HR/9, pitch efficiency, command."""
    log.info("computing_pitcher_advanced_stats")

    pitchers = df[df["outs_recorded"] > 0].copy()
    pitchers["ip"] = pitchers["outs_recorded"] / 3.0

    pitchers["bb9_raw"]          = (pitchers["walks_allowed"] / pitchers["ip"].replace(0, np.nan)) * 9
    pitchers["hr9_raw"]          = (pitchers["home_runs_allowed"] / pitchers["ip"].replace(0, np.nan)) * 9
    pitchers["pitch_eff_raw"]    = pitchers["outs_recorded"] / pitchers["pitches_thrown"].replace(0, np.nan)
    # command_rate = K / (K + BB) — pure command metric
    pitchers["command_raw"]      = (
        pitchers["strikeouts_pitcher"]
        / (pitchers["strikeouts_pitcher"] + pitchers["walks_allowed"]).replace(0, np.nan)
    )
    # Quality start: 6+ IP and 3 or fewer ER
    pitchers["qs_raw"]           = (
        (pitchers["ip"] >= 6) & (pitchers["earned_runs"] <= 3)
    ).astype(float)

    for col in ["bb9_raw", "hr9_raw", "pitch_eff_raw", "command_raw", "qs_raw"]:
        pitchers[col] = pitchers[col].fillna(0).clip(0, 20 if col in ["bb9_raw", "hr9_raw"] else 1)

    results = []
    for pid, grp in pitchers.groupby("player_id", group_keys=False):
        g = grp.sort_values(["game_date", "player_game_id"]).reset_index(drop=True)
        feats = {"player_game_id": g["player_game_id"].values}
        season_marker = (g["season"] != g["season"].shift(1)).cumsum()

        for stat, col in [
            ("pitcher_bb9",       "bb9_raw"),
            ("pitcher_hr9",       "hr9_raw"),
            ("pitcher_pitch_eff", "pitch_eff_raw"),
            ("pitcher_command",   "command_raw"),
            ("pitcher_qs_rate",   "qs_raw"),
        ]:
            pv = g[col].shift(1)
            for w in [5, 10]:
                feats[f"last_{w}_avg_{stat}"] = (
                    pv.rolling(w, min_periods=1).mean().fillna(0).round(4).values)
            feats[f"season_avg_{stat}"] = (
                g.groupby(season_marker)[col].apply(
                    lambda s: s.shift(1).expanding().mean()
                ).reset_index(level=0, drop=True).fillna(0).round(4).values)

        results.append(pd.DataFrame(feats))

    return pd.concat(results, ignore_index=True) if results else pd.DataFrame()


def compute_park_and_platoon(df: pd.DataFrame) -> pd.DataFrame:
    """Park factors and platoon advantage per player-game."""
    log.info("computing_park_and_platoon_features")
    results = []

    for pid, grp in df.groupby("player_id", group_keys=False):
        g = grp.sort_values(["game_date", "player_game_id"]).reset_index(drop=True)
        feats = {"player_game_id": g["player_game_id"].values}

        # Park factor — based on home team
        feats["park_factor"] = [
            _park_factor_for_team(str(row.home_team_name))
            for row in g.itertuples()
        ]

        # Platoon advantage proxy:
        # Without handedness: use position-based proxy
        # G (catcher), 1B, etc. vs LHP/RHP — skip for now, default 0
        feats["platoon_advantage"] = [0.0] * len(g)

        results.append(pd.DataFrame(feats))

    return pd.concat(results, ignore_index=True)


def merge_features(feature_dfs: list, batch_size: int = 3000):
    # Merge all feature DataFrames
    valid = [f for f in feature_dfs if f is not None and not f.empty]
    if not valid:
        return
    combined = valid[0]
    for fdf in valid[1:]:
        combined = combined.merge(fdf, on="player_game_id", how="outer")

    log.info("merging_mlb_advanced_features", rows=len(combined),
             features=len(combined.columns) - 1)

    feat_cols = [c for c in combined.columns if c != "player_game_id"]
    items = []
    for _, row in combined.iterrows():
        patch = {}
        for c in feat_cols:
            v = row[c]
            if pd.isna(v):
                continue
            patch[c] = round(float(v), 4) if not isinstance(v, (np.integer,)) else int(v)
        if patch:
            items.append((int(row["player_game_id"]), patch))

    with session_scope() as session:
        for i in range(0, len(items), batch_size):
            for pg_id, patch in items[i:i + batch_size]:
                session.execute(text("""
                    UPDATE player_games
                    SET derived = derived || CAST(:patch AS JSONB),
                        updated_at = NOW()
                    WHERE player_game_id = :pid
                """), {"patch": json.dumps(patch), "pid": pg_id})
            if (i // batch_size) % 10 == 0:
                log.info("merge_progress",
                         done=min(i + batch_size, len(items)), total=len(items))


def run():
    configure_logging()
    started = datetime.now()
    with session_scope() as session:
        run_id = session.execute(text("""
            INSERT INTO ingestion_runs (source, started_at, status)
            VALUES ('mlb_advanced_stats', :s, 'running') RETURNING run_id
        """), {"s": started}).scalar()

    df = load_mlb_player_games()
    df = explode_stats(df)

    batter_feats  = compute_batter_advanced(df.copy())
    pitcher_feats = compute_pitcher_advanced(df.copy())
    park_feats    = compute_park_and_platoon(df.copy())

    merge_features([batter_feats, pitcher_feats, park_feats])

    with session_scope() as session:
        session.execute(text("""
            UPDATE ingestion_runs SET completed_at=NOW(),
                rows_inserted=:n, status='success' WHERE run_id=:rid
        """), {"n": len(df), "rid": run_id})
    log.info("mlb_advanced_stats_complete", rows=len(df))


if __name__ == "__main__":
    run()
