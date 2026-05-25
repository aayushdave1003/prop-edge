-- =====================================================================
-- PROP BETTING EDGE FINDER — DATABASE SCHEMA
-- Postgres 16. Designed to handle MLB, WNBA, NBA, NFL, NHL uniformly.
-- =====================================================================

-- ---------------------------------------------------------------------
-- REFERENCE TABLES
-- ---------------------------------------------------------------------

CREATE TABLE sports (
    sport_code      TEXT PRIMARY KEY,            -- 'mlb', 'wnba', 'nba', 'nfl', 'nhl'
    display_name    TEXT NOT NULL,
    season_format   TEXT NOT NULL                -- 'calendar_year' or 'split_year'
);

CREATE TABLE teams (
    team_id         SERIAL PRIMARY KEY,
    sport_code      TEXT NOT NULL REFERENCES sports(sport_code),
    external_id     TEXT NOT NULL,               -- id from source API (nba_api, pybaseball, etc.)
    abbreviation    TEXT NOT NULL,               -- 'LAL', 'NYY'
    name            TEXT NOT NULL,
    city            TEXT,
    UNIQUE (sport_code, external_id)
);

CREATE TABLE players (
    player_id       SERIAL PRIMARY KEY,
    sport_code      TEXT NOT NULL REFERENCES sports(sport_code),
    external_id     TEXT NOT NULL,
    full_name       TEXT NOT NULL,
    position        TEXT,
    handedness      TEXT,                        -- 'L'/'R'/'S' for batters, 'L'/'R' for pitchers, null for others
    current_team_id INTEGER REFERENCES teams(team_id),
    active          BOOLEAN DEFAULT TRUE,
    UNIQUE (sport_code, external_id)
);

CREATE INDEX idx_players_sport_active ON players(sport_code, active);
CREATE INDEX idx_players_name_trgm ON players USING gin (full_name gin_trgm_ops);  -- for fuzzy name matching across data sources

-- ---------------------------------------------------------------------
-- GAMES
-- ---------------------------------------------------------------------

CREATE TABLE games (
    game_id         SERIAL PRIMARY KEY,
    sport_code      TEXT NOT NULL REFERENCES sports(sport_code),
    external_id     TEXT NOT NULL,
    game_date       DATE NOT NULL,
    game_datetime   TIMESTAMPTZ,                 -- exact start time, for cutoffs
    season          TEXT NOT NULL,               -- '2026' for MLB, '2025-26' for NBA
    season_type     TEXT NOT NULL,               -- 'regular', 'playoff', 'preseason'
    home_team_id    INTEGER NOT NULL REFERENCES teams(team_id),
    away_team_id    INTEGER NOT NULL REFERENCES teams(team_id),
    home_score      INTEGER,
    away_score      INTEGER,
    status          TEXT NOT NULL DEFAULT 'scheduled',  -- 'scheduled', 'in_progress', 'final', 'postponed'

    -- Sport-specific context lives in JSONB to keep the table tidy
    -- MLB: weather, wind_dir_deg, wind_speed_mph, temperature_f, umpire_name, ballpark
    -- NFL: weather, dome (bool), temperature_f, wind_speed_mph, surface
    -- NBA/WNBA: pace_estimate, back_to_back_home, back_to_back_away
    -- NHL: starting_goalies (dict)
    context         JSONB DEFAULT '{}'::jsonb,

    UNIQUE (sport_code, external_id)
);

CREATE INDEX idx_games_date ON games(game_date);
CREATE INDEX idx_games_sport_date ON games(sport_code, game_date);
CREATE INDEX idx_games_status ON games(status) WHERE status != 'final';
CREATE INDEX idx_games_context_gin ON games USING gin (context);

-- ---------------------------------------------------------------------
-- PLAYER GAME PERFORMANCES (the universal stats table)
-- ---------------------------------------------------------------------
-- One row per (player, game). Sport-specific stats in JSONB.
-- This is denormalized for query speed; the alternative (one row per
-- stat) makes feature engineering painful.

CREATE TABLE player_games (
    player_game_id  SERIAL PRIMARY KEY,
    player_id       INTEGER NOT NULL REFERENCES players(player_id),
    game_id         INTEGER NOT NULL REFERENCES games(game_id),
    team_id         INTEGER NOT NULL REFERENCES teams(team_id),
    opponent_id     INTEGER NOT NULL REFERENCES teams(team_id),
    is_home         BOOLEAN NOT NULL,
    started         BOOLEAN,
    did_play        BOOLEAN NOT NULL DEFAULT TRUE,
    minutes_played  NUMERIC(5,2),                -- null for MLB/NFL where it doesn't apply cleanly

    -- All raw box-score stats live here. Keys differ by sport.
    -- MLB batter: at_bats, hits, runs, rbis, home_runs, total_bases, strikeouts, walks, stolen_bases
    -- MLB pitcher: innings_pitched, hits_allowed, earned_runs, strikeouts, walks_allowed, pitches_thrown
    -- NBA/WNBA: points, rebounds, assists, steals, blocks, threes_made, threes_attempted, fg_made, fg_attempted, fts_made, fts_attempted, turnovers, fouls
    -- NFL QB: pass_yards, pass_tds, interceptions, completions, attempts, rush_yards, rush_tds
    -- NFL skill: rush_yards, rush_tds, rec_yards, rec_tds, receptions, targets
    -- NHL skater: goals, assists, points, shots_on_goal, blocked_shots, hits, plus_minus, toi_seconds
    -- NHL goalie: saves, goals_against, shots_against, save_pct, win
    stats           JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Computed convenience fields populated by feature pipeline
    -- (cached here to avoid recomputing on every query)
    derived         JSONB DEFAULT '{}'::jsonb,

    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE (player_id, game_id)
);

CREATE INDEX idx_pg_player ON player_games(player_id);
CREATE INDEX idx_pg_game ON player_games(game_id);
CREATE INDEX idx_pg_team_game ON player_games(team_id, game_id);
CREATE INDEX idx_pg_stats_gin ON player_games USING gin (stats);

-- ---------------------------------------------------------------------
-- PROP LINES (the offers from PrizePicks, Underdog, sportsbooks)
-- ---------------------------------------------------------------------
-- Lines change throughout the day. We store every snapshot so we can
-- backtest against closing lines and track line movement.

CREATE TABLE prop_lines (
    line_id         BIGSERIAL PRIMARY KEY,
    sportsbook      TEXT NOT NULL,               -- 'prizepicks', 'underdog', 'draftkings', 'fanduel'
    sport_code      TEXT NOT NULL REFERENCES sports(sport_code),
    player_id       INTEGER NOT NULL REFERENCES players(player_id),
    game_id         INTEGER NOT NULL REFERENCES games(game_id),
    stat_type       TEXT NOT NULL,               -- 'points', 'rebounds', 'pass_yards', 'strikeouts_pitcher', etc.
    line_value      NUMERIC(8,3) NOT NULL,       -- 22.5, 305.5, 7.5
    over_payout     NUMERIC(6,3),                -- decimal odds for traditional books; null for pick'em
    under_payout    NUMERIC(6,3),
    is_pickem       BOOLEAN NOT NULL DEFAULT FALSE,  -- true for PrizePicks/Underdog
    snapshot_at     TIMESTAMPTZ NOT NULL,
    is_closing      BOOLEAN DEFAULT FALSE        -- flagged true after game starts; the final pre-game line
);

CREATE INDEX idx_pl_game ON prop_lines(game_id);
CREATE INDEX idx_pl_player_stat ON prop_lines(player_id, stat_type);
CREATE INDEX idx_pl_snapshot ON prop_lines(snapshot_at);
CREATE INDEX idx_pl_book_closing ON prop_lines(sportsbook, is_closing) WHERE is_closing = TRUE;
CREATE INDEX idx_pl_lookup ON prop_lines(player_id, game_id, stat_type, sportsbook, snapshot_at DESC);

-- ---------------------------------------------------------------------
-- MODEL PREDICTIONS
-- ---------------------------------------------------------------------
-- One row per (model_version, player, game, stat_type). We store the
-- full predicted distribution as parameters, plus point estimates for
-- quick filtering.

CREATE TABLE model_versions (
    model_version_id SERIAL PRIMARY KEY,
    sport_code      TEXT NOT NULL REFERENCES sports(sport_code),
    stat_type       TEXT NOT NULL,
    name            TEXT NOT NULL,               -- 'lgbm_v1', 'baseline_rolling10'
    trained_at      TIMESTAMPTZ NOT NULL,
    training_window TEXT,                        -- e.g. '2023-01-01_to_2026-05-31'
    notes           TEXT,
    UNIQUE (sport_code, stat_type, name)
);

CREATE TABLE predictions (
    prediction_id   BIGSERIAL PRIMARY KEY,
    model_version_id INTEGER NOT NULL REFERENCES model_versions(model_version_id),
    player_id       INTEGER NOT NULL REFERENCES players(player_id),
    game_id         INTEGER NOT NULL REFERENCES games(game_id),
    stat_type       TEXT NOT NULL,
    predicted_mean  NUMERIC(8,3) NOT NULL,
    predicted_std   NUMERIC(8,3),
    -- Distribution: 'normal', 'poisson', 'neg_binomial', 'empirical_quantiles'
    -- Parameters depend on distribution.
    distribution    TEXT NOT NULL,
    dist_params     JSONB NOT NULL,              -- e.g. {"mu": 22.4, "alpha": 0.31} for NB
    predicted_at    TIMESTAMPTZ NOT NULL,
    UNIQUE (model_version_id, player_id, game_id, stat_type, predicted_at)
);

CREATE INDEX idx_pred_game ON predictions(game_id);
CREATE INDEX idx_pred_player_stat ON predictions(player_id, stat_type);

-- ---------------------------------------------------------------------
-- PICKS LOG (the paper-trading record — the heart of the writeup)
-- ---------------------------------------------------------------------
-- Every pick the system would have made, with all context needed to
-- audit it later. Never modify rows after insertion.

CREATE TABLE picks (
    pick_id         BIGSERIAL PRIMARY KEY,
    pick_group_id   UUID,                        -- groups legs of a parlay; null for singles
    parlay_size     SMALLINT NOT NULL DEFAULT 1, -- 1 for single, 2-6 for parlays
    parlay_payout   NUMERIC(8,3),                -- multiplier for parlays (PrizePicks 6-pick = ~25x)

    sport_code      TEXT NOT NULL REFERENCES sports(sport_code),
    player_id       INTEGER NOT NULL REFERENCES players(player_id),
    game_id         INTEGER NOT NULL REFERENCES games(game_id),
    stat_type       TEXT NOT NULL,
    line_id         BIGINT NOT NULL REFERENCES prop_lines(line_id),
    direction       TEXT NOT NULL CHECK (direction IN ('over', 'under')),

    model_version_id INTEGER NOT NULL REFERENCES model_versions(model_version_id),
    prediction_id   BIGINT NOT NULL REFERENCES predictions(prediction_id),
    model_prob      NUMERIC(6,4) NOT NULL,       -- model's P(direction wins)
    edge            NUMERIC(6,4) NOT NULL,       -- model_prob - implied_prob
    expected_value  NUMERIC(8,4),                -- in units of stake; positive = +EV

    -- Outcome (filled after game completes)
    actual_value    NUMERIC(8,3),
    leg_result      TEXT CHECK (leg_result IN ('win', 'loss', 'push', 'void')),
    parlay_result   TEXT CHECK (parlay_result IN ('win', 'loss', 'push', 'void')),  -- filled per pick_group

    picked_at       TIMESTAMPTZ NOT NULL,
    settled_at      TIMESTAMPTZ
);

CREATE INDEX idx_picks_group ON picks(pick_group_id);
CREATE INDEX idx_picks_date ON picks(picked_at);
CREATE INDEX idx_picks_sport ON picks(sport_code, picked_at);
CREATE INDEX idx_picks_unsettled ON picks(game_id) WHERE leg_result IS NULL;

-- ---------------------------------------------------------------------
-- CORRELATION CACHE
-- ---------------------------------------------------------------------
-- Precomputed pairwise correlations for parlay optimization.
-- Populated by an offline job, queried by the optimizer.

CREATE TABLE stat_correlations (
    correlation_id  SERIAL PRIMARY KEY,
    sport_code      TEXT NOT NULL REFERENCES sports(sport_code),
    context         TEXT NOT NULL,               -- 'same_team', 'opposing_team', 'same_game_any'
    stat_a          TEXT NOT NULL,
    stat_b          TEXT NOT NULL,
    -- Correlation of (player_A_over_indicator, player_B_over_indicator)
    correlation     NUMERIC(6,4) NOT NULL,
    sample_size     INTEGER NOT NULL,
    computed_at     TIMESTAMPTZ NOT NULL,
    UNIQUE (sport_code, context, stat_a, stat_b)
);

-- ---------------------------------------------------------------------
-- DATA QUALITY / INGESTION LOG
-- ---------------------------------------------------------------------
-- When scrapers run, log success/failure. Saves you when something
-- breaks silently in mid-August and you don't notice for a week.

CREATE TABLE ingestion_runs (
    run_id          BIGSERIAL PRIMARY KEY,
    source          TEXT NOT NULL,               -- 'pybaseball_games', 'prizepicks_props', 'odds_api_mlb'
    started_at      TIMESTAMPTZ NOT NULL,
    completed_at    TIMESTAMPTZ,
    rows_inserted   INTEGER,
    rows_updated    INTEGER,
    status          TEXT NOT NULL,               -- 'success', 'partial', 'failed'
    error_message   TEXT
);

CREATE INDEX idx_ingestion_recent ON ingestion_runs(source, started_at DESC);

-- ---------------------------------------------------------------------
-- SEED REFERENCE DATA
-- ---------------------------------------------------------------------

INSERT INTO sports (sport_code, display_name, season_format) VALUES
    ('mlb',  'Major League Baseball',     'calendar_year'),
    ('wnba', 'WNBA',                       'calendar_year'),
    ('nba',  'NBA',                        'split_year'),
    ('nfl',  'NFL',                        'split_year'),
    ('nhl',  'NHL',                        'split_year');

-- Required extensions
CREATE EXTENSION IF NOT EXISTS pg_trgm;          -- fuzzy text matching for player names
