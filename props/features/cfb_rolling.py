"""Rolling features for CFB player-games (sport_code='cfb'). Identical to
nfl_rolling — college football has the same passing/rushing/receiving stats."""
from datetime import datetime
import pandas as pd
from sqlalchemy import text
from props.utils.db import engine, session_scope
from props.utils.logging import log, configure_logging
from props.features.derived_writer import write_derived, feat_dict
from props.features.nfl_rolling import ROLL_STATS, compute_rolling_for_player


def load_player_games() -> pd.DataFrame:
    log.info("loading_cfb_player_games")
    df = pd.read_sql("""
        SELECT pg.player_game_id, pg.player_id, pg.game_id, g.game_date, g.season, pg.stats
        FROM player_games pg JOIN games g USING (game_id)
        WHERE g.sport_code = 'cfb'
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


def run():
    configure_logging()
    started = datetime.now()
    with session_scope() as session:
        run_id = session.execute(text("""
            INSERT INTO ingestion_runs (source, started_at, status)
            VALUES ('cfb_rolling', :s, 'running') RETURNING run_id
        """), {"s": started}).scalar()
    df = load_player_games()
    if df.empty:
        log.info("no_cfb_games_yet")
        return
    df = explode_stats(df)
    out = pd.concat([compute_rolling_for_player(grp) for _, grp in df.groupby("player_id", group_keys=False)],
                    ignore_index=True)
    log.info("rolling_features_computed", rows=len(out), features=len(out.columns) - 1)
    cols = [c for c in out.columns if c != "player_game_id"]
    write_derived([(int(r["player_game_id"]), feat_dict(r, cols)) for _, r in out.iterrows()],
                  mode="replace", label="cfb_rolling")
    with session_scope() as session:
        session.execute(text("UPDATE ingestion_runs SET completed_at=NOW(), rows_inserted=:n, status='success' WHERE run_id=:rid"),
                        {"n": len(out), "rid": run_id})
    log.info("cfb_rolling_complete", updated=len(out))


if __name__ == "__main__":
    run()
