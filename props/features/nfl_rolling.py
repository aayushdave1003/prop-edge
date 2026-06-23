"""Compute rolling features for NFL player-games (sport_code='nfl').

NFL plays weekly (~17 games/season), so windows are short: last 3/5/8 + season.
Covers the skill-position stats the props market prices (passing/rushing/receiving
yards, receptions, TDs). Self-contained — mirrors cbb_rolling.
"""
from datetime import datetime
import pandas as pd
from sqlalchemy import text
from props.utils.db import engine, session_scope
from props.utils.logging import log, configure_logging
from props.features.derived_writer import write_derived, feat_dict

WINDOWS = [3, 5, 8]

ROLL_STATS = [
    "passing_yards", "passing_tds", "completions", "pass_attempts", "interceptions",
    "rushing_yards", "rushing_tds", "carries",
    "receiving_yards", "receiving_tds", "receptions", "targets",
]


def load_player_games() -> pd.DataFrame:
    log.info("loading_nfl_player_games")
    df = pd.read_sql("""
        SELECT pg.player_game_id, pg.player_id, pg.game_id, g.game_date, g.season, pg.stats
        FROM player_games pg JOIN games g USING (game_id)
        WHERE g.sport_code = 'nfl'
        ORDER BY pg.player_id, g.game_date, pg.player_game_id
    """, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    log.info("loaded", n=len(df))
    return df


def explode_stats(df: pd.DataFrame) -> pd.DataFrame:
    stats = pd.json_normalize(df["stats"])
    out = df.drop(columns=["stats"]).reset_index(drop=True)
    for col in ROLL_STATS:
        out[col] = pd.to_numeric(stats.get(col, 0), errors="coerce").fillna(0)
    return out


def compute_rolling_for_player(group: pd.DataFrame) -> pd.DataFrame:
    g = group.sort_values(["game_date", "player_game_id"]).reset_index(drop=True)
    feats = {"player_game_id": g["player_game_id"].values}
    feats["days_rest"] = (g["game_date"] - g["game_date"].shift(1)).dt.days.fillna(-1).astype(int).values
    season_marker = (g["season"] != g["season"].shift(1)).cumsum()
    feats["games_played_season"] = g.groupby(season_marker).cumcount().values
    for stat in ROLL_STATS:
        pv = g[stat].shift(1)
        for w in WINDOWS:
            feats[f"last_{w}_avg_{stat}"] = pv.rolling(w, min_periods=1).mean().fillna(0).round(3).values
        cum = g.groupby(season_marker)[stat].apply(
            lambda s: s.shift(1).expanding().mean()).reset_index(level=0, drop=True).fillna(0)
        feats[f"season_avg_{stat}"] = cum.round(3).values
    return pd.DataFrame(feats)


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    log.info("computing_nfl_rolling_features")
    out = pd.concat([compute_rolling_for_player(grp) for _, grp in df.groupby("player_id", group_keys=False)],
                    ignore_index=True)
    log.info("rolling_features_computed", rows=len(out), features=len(out.columns) - 1)
    return out


def write_to_derived(feature_df: pd.DataFrame):
    cols = [c for c in feature_df.columns if c != "player_game_id"]
    items = [(int(row["player_game_id"]), feat_dict(row, cols)) for _, row in feature_df.iterrows()]
    write_derived(items, mode="replace", label="nfl_rolling")


def run():
    configure_logging()
    started = datetime.now()
    with session_scope() as session:
        run_id = session.execute(text("""
            INSERT INTO ingestion_runs (source, started_at, status)
            VALUES ('nfl_rolling', :s, 'running') RETURNING run_id
        """), {"s": started}).scalar()
    df = load_player_games()
    if df.empty:
        log.info("no_nfl_games_yet")
        return
    out = compute_all(explode_stats(df))
    write_to_derived(out)
    with session_scope() as session:
        session.execute(text("""
            UPDATE ingestion_runs SET completed_at=NOW(), rows_inserted=:n, status='success' WHERE run_id=:rid
        """), {"n": len(out), "rid": run_id})
    log.info("nfl_rolling_complete", updated=len(out))


if __name__ == "__main__":
    run()
