"""WNBA Basketball IQ Features — identical logic to NBA, sport_code='wnba'.

Usage rate, floor spacing, foul drawing, paint scoring, AST/PTS ratio,
opponent spacing, teammate spacing, game script, career vs opponent,
opponent pts by position. All computed from WNBA box scores.
"""
from datetime import datetime
import pandas as pd
from sqlalchemy import text
from props.utils.db import engine, session_scope
from props.utils.logging import log, configure_logging

# Reuse all computation logic from NBA script, just change sport_code
from props.features.nba_basketball_iq import (
    explode_stats, compute_player_role_features, compute_spacing_context,
    compute_game_script_features, compute_career_vs_opponent,
    compute_opp_pts_by_position, merge_features
)

WINDOWS = [5, 10, 20]


def load_wnba_player_games() -> pd.DataFrame:
    log.info("loading_wnba_player_games_for_biq")
    df = pd.read_sql("""
        SELECT pg.player_game_id, pg.player_id, pg.game_id, pg.team_id,
               pg.opponent_id, pg.is_home, pg.minutes_played,
               pg.stats, g.game_date, g.season, p.position
        FROM player_games pg
        JOIN games g USING (game_id)
        JOIN players p ON p.player_id = pg.player_id
        WHERE g.sport_code = 'wnba'
        ORDER BY pg.player_id, g.game_date, pg.player_game_id
    """, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    log.info("loaded", rows=len(df))
    return df


def load_wnba_game_scores() -> pd.DataFrame:
    return pd.read_sql("""
        SELECT game_id, game_date, home_team_id, away_team_id,
               home_score, away_score,
               ABS(home_score - away_score) AS margin
        FROM games
        WHERE sport_code = 'wnba' AND status = 'final'
          AND home_score IS NOT NULL
    """, engine)


def run():
    configure_logging()
    started = datetime.now()
    with session_scope() as session:
        run_id = session.execute(text("""
            INSERT INTO ingestion_runs (source, started_at, status)
            VALUES ('wnba_basketball_iq', :s, 'running') RETURNING run_id
        """), {"s": started}).scalar()

    df     = load_wnba_player_games()
    if df.empty:
        log.info("no_wnba_data_yet")
        return

    scores = load_wnba_game_scores()
    df     = explode_stats(df)

    role_feats    = compute_player_role_features(df.copy())
    spacing_feats = compute_spacing_context(df.copy())
    script_feats  = compute_game_script_features(df.copy(), scores)
    career_feats  = compute_career_vs_opponent(df.copy())
    pos_feats     = compute_opp_pts_by_position(df.copy())

    merge_features([role_feats, spacing_feats, script_feats,
                    career_feats, pos_feats])

    with session_scope() as session:
        session.execute(text("""
            UPDATE ingestion_runs SET completed_at=NOW(),
                rows_inserted=:n, status='success' WHERE run_id=:rid
        """), {"n": len(role_feats), "rid": run_id})
    log.info("wnba_basketball_iq_complete", rows=len(role_feats))


if __name__ == "__main__":
    run()
