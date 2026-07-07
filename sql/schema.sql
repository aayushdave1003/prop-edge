-- =====================================================================
-- prop-edge — DATABASE SCHEMA (PostgreSQL 18)
-- AUTO-GENERATED from the live prod schema via SQLAlchemy reflection.
-- Canonical regeneration (byte-exact pg_dump): scripts/dump_schema.sh
-- Do not hand-edit; change the DB via props/maintenance/migrate.py then regen.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- similarity() name matching

CREATE TABLE public.backtest_daily (
	run_date DATE NOT NULL, 
	window_days INTEGER NOT NULL, 
	rec_n INTEGER, 
	rec_w INTEGER, 
	rec_l INTEGER, 
	rec_winrate DOUBLE PRECISION, 
	rec_roi_2pick DOUBLE PRECISION, 
	all_n INTEGER, 
	all_winrate DOUBLE PRECISION, 
	brier DOUBLE PRECISION, 
	detail JSONB, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT backtest_daily_pkey PRIMARY KEY (run_date)
);

CREATE TABLE public.backtest_runs (
	run_id BIGSERIAL NOT NULL, 
	run_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	sport TEXT, 
	since_date DATE NOT NULL, 
	n_picks INTEGER, 
	win_rate NUMERIC(6, 4), 
	roi_2pick NUMERIC(6, 4), 
	edge_10_win_rate NUMERIC(6, 4), 
	edge_10_n INTEGER, 
	calibration_gap NUMERIC(6, 4), 
	trigger TEXT DEFAULT 'manual'::text, 
	mae_improvement_pct DOUBLE PRECISION, 
	CONSTRAINT backtest_runs_pkey PRIMARY KEY (run_id)
);
CREATE INDEX backtest_runs_sport_run_at ON public.backtest_runs (sport, run_at DESC);

CREATE TABLE public.game_weather (
	game_id INTEGER NOT NULL, 
	temp_f NUMERIC(6, 1), 
	wind_mph NUMERIC(6, 1), 
	wind_dir NUMERIC(6, 1), 
	wind_out_mph NUMERIC(6, 1), 
	humidity NUMERIC(6, 1), 
	is_dome BOOLEAN, 
	fetched_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT game_weather_pkey PRIMARY KEY (game_id)
);

CREATE TABLE public.ingestion_runs (
	run_id BIGSERIAL NOT NULL, 
	source TEXT NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	completed_at TIMESTAMP WITH TIME ZONE, 
	rows_inserted INTEGER, 
	rows_updated INTEGER, 
	status TEXT NOT NULL, 
	error_message TEXT, 
	CONSTRAINT ingestion_runs_pkey PRIMARY KEY (run_id)
);
CREATE INDEX idx_ingestion_recent ON public.ingestion_runs (source, started_at DESC);

CREATE TABLE public.player_injuries (
	player_name TEXT NOT NULL, 
	team_name TEXT NOT NULL, 
	status TEXT NOT NULL, 
	short_comment TEXT, 
	fetched_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	sport_code TEXT DEFAULT 'nba'::text NOT NULL, 
	CONSTRAINT player_injuries_pkey PRIMARY KEY (player_name, team_name, sport_code, fetched_at)
);
CREATE INDEX idx_injuries_sport_player_recent ON public.player_injuries (sport_code, player_name, fetched_at DESC);

CREATE TABLE public.schema_migrations (
	id TEXT NOT NULL, 
	applied_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	CONSTRAINT schema_migrations_pkey PRIMARY KEY (id)
);

CREATE TABLE public.scored_props (
	id BIGSERIAL NOT NULL, 
	score_date DATE NOT NULL, 
	sport_code TEXT NOT NULL, 
	game_id INTEGER NOT NULL, 
	player_id INTEGER NOT NULL, 
	stat_type TEXT NOT NULL, 
	line_value NUMERIC(8, 2) NOT NULL, 
	direction TEXT, 
	model_prob DOUBLE PRECISION, 
	edge DOUBLE PRECISION, 
	ev DOUBLE PRECISION, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT scored_props_pkey PRIMARY KEY (id), 
	CONSTRAINT scored_props_game_id_player_id_stat_type_line_value_key UNIQUE NULLS DISTINCT (game_id, player_id, stat_type, line_value)
);
CREATE INDEX idx_scored_props_score_date ON public.scored_props (score_date);

CREATE TABLE public.soft_lines (
	run_date DATE NOT NULL, 
	sport_code TEXT, 
	player_name TEXT NOT NULL, 
	stat_type TEXT NOT NULL, 
	pp_line NUMERIC(8, 2) NOT NULL, 
	sharp_line NUMERIC(8, 2), 
	sharp_over_prob NUMERIC(8, 4), 
	best_side TEXT, 
	best_prob NUMERIC(8, 4), 
	edge NUMERIC(8, 4), 
	game_id INTEGER, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT soft_lines_pkey PRIMARY KEY (run_date, player_name, stat_type, pp_line)
);

CREATE TABLE public.sports (
	sport_code TEXT NOT NULL, 
	display_name TEXT NOT NULL, 
	season_format TEXT NOT NULL, 
	CONSTRAINT sports_pkey PRIMARY KEY (sport_code)
);

CREATE TABLE public.model_versions (
	model_version_id SERIAL NOT NULL, 
	sport_code TEXT NOT NULL, 
	stat_type TEXT NOT NULL, 
	name TEXT NOT NULL, 
	trained_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	training_window TEXT, 
	notes TEXT, 
	CONSTRAINT model_versions_pkey PRIMARY KEY (model_version_id), 
	CONSTRAINT model_versions_sport_code_fkey FOREIGN KEY(sport_code) REFERENCES public.sports (sport_code), 
	CONSTRAINT model_versions_sport_code_stat_type_name_key UNIQUE NULLS DISTINCT (sport_code, stat_type, name)
);

CREATE TABLE public.stat_correlations (
	correlation_id SERIAL NOT NULL, 
	sport_code TEXT NOT NULL, 
	context TEXT NOT NULL, 
	stat_a TEXT NOT NULL, 
	stat_b TEXT NOT NULL, 
	correlation NUMERIC(6, 4) NOT NULL, 
	sample_size INTEGER NOT NULL, 
	computed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT stat_correlations_pkey PRIMARY KEY (correlation_id), 
	CONSTRAINT stat_correlations_sport_code_fkey FOREIGN KEY(sport_code) REFERENCES public.sports (sport_code), 
	CONSTRAINT stat_correlations_sport_code_context_stat_a_stat_b_key UNIQUE NULLS DISTINCT (sport_code, context, stat_a, stat_b)
);

CREATE TABLE public.teams (
	team_id SERIAL NOT NULL, 
	sport_code TEXT NOT NULL, 
	external_id TEXT NOT NULL, 
	abbreviation TEXT NOT NULL, 
	name TEXT NOT NULL, 
	city TEXT, 
	CONSTRAINT teams_pkey PRIMARY KEY (team_id), 
	CONSTRAINT teams_sport_code_fkey FOREIGN KEY(sport_code) REFERENCES public.sports (sport_code), 
	CONSTRAINT teams_sport_code_external_id_key UNIQUE NULLS DISTINCT (sport_code, external_id)
);

CREATE TABLE public.games (
	game_id SERIAL NOT NULL, 
	sport_code TEXT NOT NULL, 
	external_id TEXT NOT NULL, 
	game_date DATE NOT NULL, 
	game_datetime TIMESTAMP WITH TIME ZONE, 
	season TEXT NOT NULL, 
	season_type TEXT NOT NULL, 
	home_team_id INTEGER NOT NULL, 
	away_team_id INTEGER NOT NULL, 
	home_score INTEGER, 
	away_score INTEGER, 
	status TEXT DEFAULT 'scheduled'::text NOT NULL, 
	context JSONB DEFAULT '{}'::jsonb, 
	CONSTRAINT games_pkey PRIMARY KEY (game_id), 
	CONSTRAINT games_away_team_id_fkey FOREIGN KEY(away_team_id) REFERENCES public.teams (team_id), 
	CONSTRAINT games_home_team_id_fkey FOREIGN KEY(home_team_id) REFERENCES public.teams (team_id), 
	CONSTRAINT games_sport_code_fkey FOREIGN KEY(sport_code) REFERENCES public.sports (sport_code), 
	CONSTRAINT games_sport_code_external_id_key UNIQUE NULLS DISTINCT (sport_code, external_id)
);
CREATE INDEX idx_games_context_gin ON public.games USING gin (context);
CREATE INDEX idx_games_date ON public.games (game_date);
CREATE INDEX idx_games_sport_date ON public.games (sport_code, game_date);
CREATE INDEX idx_games_status ON public.games (status) WHERE (status <> 'final'::text);

CREATE TABLE public.players (
	player_id SERIAL NOT NULL, 
	sport_code TEXT NOT NULL, 
	external_id TEXT NOT NULL, 
	full_name TEXT NOT NULL, 
	position TEXT, 
	handedness TEXT, 
	current_team_id INTEGER, 
	active BOOLEAN DEFAULT true, 
	photo_url TEXT, 
	CONSTRAINT players_pkey PRIMARY KEY (player_id), 
	CONSTRAINT players_current_team_id_fkey FOREIGN KEY(current_team_id) REFERENCES public.teams (team_id), 
	CONSTRAINT players_sport_code_fkey FOREIGN KEY(sport_code) REFERENCES public.sports (sport_code), 
	CONSTRAINT players_sport_code_external_id_key UNIQUE NULLS DISTINCT (sport_code, external_id)
);
CREATE INDEX idx_players_sport_active ON public.players (sport_code, active);

CREATE TABLE public.market_odds (
	market_odd_id BIGSERIAL NOT NULL, 
	game_id INTEGER NOT NULL, 
	player_id INTEGER, 
	stat_type TEXT NOT NULL, 
	line_value NUMERIC(7, 2) NOT NULL, 
	over_price INTEGER, 
	under_price INTEGER, 
	market_over_prob NUMERIC(6, 4), 
	bookmaker TEXT NOT NULL, 
	snapshot_time TIMESTAMP WITH TIME ZONE NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	CONSTRAINT market_odds_pkey PRIMARY KEY (market_odd_id), 
	CONSTRAINT market_odds_game_id_fkey FOREIGN KEY(game_id) REFERENCES public.games (game_id), 
	CONSTRAINT market_odds_player_id_fkey FOREIGN KEY(player_id) REFERENCES public.players (player_id), 
	CONSTRAINT market_odds_game_id_player_id_stat_type_line_value_bookmake_key UNIQUE NULLS DISTINCT (game_id, player_id, stat_type, line_value, bookmaker)
);
CREATE INDEX market_odds_game_player ON public.market_odds (game_id, player_id, stat_type);

CREATE TABLE public.player_games (
	player_game_id SERIAL NOT NULL, 
	player_id INTEGER NOT NULL, 
	game_id INTEGER NOT NULL, 
	team_id INTEGER NOT NULL, 
	opponent_id INTEGER NOT NULL, 
	is_home BOOLEAN NOT NULL, 
	started BOOLEAN, 
	did_play BOOLEAN DEFAULT true NOT NULL, 
	minutes_played NUMERIC(5, 2), 
	stats JSONB DEFAULT '{}'::jsonb NOT NULL, 
	derived JSONB DEFAULT '{}'::jsonb, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
	CONSTRAINT player_games_pkey PRIMARY KEY (player_game_id), 
	CONSTRAINT player_games_game_id_fkey FOREIGN KEY(game_id) REFERENCES public.games (game_id), 
	CONSTRAINT player_games_opponent_id_fkey FOREIGN KEY(opponent_id) REFERENCES public.teams (team_id), 
	CONSTRAINT player_games_player_id_fkey FOREIGN KEY(player_id) REFERENCES public.players (player_id), 
	CONSTRAINT player_games_team_id_fkey FOREIGN KEY(team_id) REFERENCES public.teams (team_id), 
	CONSTRAINT player_games_player_id_game_id_key UNIQUE NULLS DISTINCT (player_id, game_id)
);
CREATE INDEX idx_pg_game ON public.player_games (game_id);
CREATE INDEX idx_pg_player ON public.player_games (player_id);
CREATE INDEX idx_pg_team_game ON public.player_games (team_id, game_id);

CREATE TABLE public.predictions (
	prediction_id BIGSERIAL NOT NULL, 
	model_version_id INTEGER NOT NULL, 
	player_id INTEGER NOT NULL, 
	game_id INTEGER NOT NULL, 
	stat_type TEXT NOT NULL, 
	predicted_mean NUMERIC(8, 3) NOT NULL, 
	predicted_std NUMERIC(8, 3), 
	distribution TEXT NOT NULL, 
	dist_params JSONB NOT NULL, 
	predicted_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT predictions_pkey PRIMARY KEY (prediction_id), 
	CONSTRAINT predictions_game_id_fkey FOREIGN KEY(game_id) REFERENCES public.games (game_id), 
	CONSTRAINT predictions_model_version_id_fkey FOREIGN KEY(model_version_id) REFERENCES public.model_versions (model_version_id), 
	CONSTRAINT predictions_player_id_fkey FOREIGN KEY(player_id) REFERENCES public.players (player_id), 
	CONSTRAINT predictions_model_version_id_player_id_game_id_stat_type_pr_key UNIQUE NULLS DISTINCT (model_version_id, player_id, game_id, stat_type, predicted_at)
);
CREATE INDEX idx_pred_game ON public.predictions (game_id);
CREATE INDEX idx_pred_player_stat ON public.predictions (player_id, stat_type);

CREATE TABLE public.prop_lines (
	line_id BIGSERIAL NOT NULL, 
	sportsbook TEXT NOT NULL, 
	sport_code TEXT NOT NULL, 
	player_id INTEGER NOT NULL, 
	game_id INTEGER NOT NULL, 
	stat_type TEXT NOT NULL, 
	line_value NUMERIC(8, 3) NOT NULL, 
	over_payout NUMERIC(6, 3), 
	under_payout NUMERIC(6, 3), 
	is_pickem BOOLEAN DEFAULT false NOT NULL, 
	snapshot_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	is_closing BOOLEAN DEFAULT false, 
	line_variant TEXT DEFAULT 'standard'::text NOT NULL, 
	CONSTRAINT prop_lines_pkey PRIMARY KEY (line_id), 
	CONSTRAINT prop_lines_game_id_fkey FOREIGN KEY(game_id) REFERENCES public.games (game_id), 
	CONSTRAINT prop_lines_player_id_fkey FOREIGN KEY(player_id) REFERENCES public.players (player_id), 
	CONSTRAINT prop_lines_sport_code_fkey FOREIGN KEY(sport_code) REFERENCES public.sports (sport_code)
);
CREATE INDEX idx_pl_lookup ON public.prop_lines (player_id, game_id, stat_type, sportsbook, snapshot_at DESC);
CREATE INDEX idx_pl_player_stat ON public.prop_lines (player_id, stat_type);
CREATE INDEX idx_pl_snapshot ON public.prop_lines (snapshot_at);
CREATE INDEX idx_pl_variant ON public.prop_lines (sport_code, stat_type, line_variant);

CREATE TABLE public.picks (
	pick_id BIGSERIAL NOT NULL, 
	pick_group_id UUID, 
	parlay_size SMALLINT DEFAULT 1 NOT NULL, 
	parlay_payout NUMERIC(8, 3), 
	sport_code TEXT NOT NULL, 
	player_id INTEGER NOT NULL, 
	game_id INTEGER NOT NULL, 
	stat_type TEXT NOT NULL, 
	line_id BIGINT NOT NULL, 
	direction TEXT NOT NULL, 
	model_version_id INTEGER NOT NULL, 
	prediction_id BIGINT NOT NULL, 
	model_prob NUMERIC(6, 4) NOT NULL, 
	edge NUMERIC(6, 4) NOT NULL, 
	expected_value NUMERIC(8, 4), 
	actual_value NUMERIC(8, 3), 
	leg_result TEXT, 
	parlay_result TEXT, 
	picked_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	settled_at TIMESTAMP WITH TIME ZONE, 
	market_edge NUMERIC(6, 4), 
	line_open NUMERIC(8, 3), 
	line_movement NUMERIC(6, 3), 
	injury_flag NUMERIC(6, 1) DEFAULT 0, 
	line_close NUMERIC(8, 3), 
	model_prob_raw NUMERIC(8, 4), 
	market_prob NUMERIC(8, 4), 
	market_prob_close NUMERIC(8, 4), 
	CONSTRAINT picks_pkey PRIMARY KEY (pick_id), 
	CONSTRAINT picks_game_id_fkey FOREIGN KEY(game_id) REFERENCES public.games (game_id), 
	CONSTRAINT picks_model_version_id_fkey FOREIGN KEY(model_version_id) REFERENCES public.model_versions (model_version_id), 
	CONSTRAINT picks_player_id_fkey FOREIGN KEY(player_id) REFERENCES public.players (player_id), 
	CONSTRAINT picks_prediction_id_fkey FOREIGN KEY(prediction_id) REFERENCES public.predictions (prediction_id), 
	CONSTRAINT picks_sport_code_fkey FOREIGN KEY(sport_code) REFERENCES public.sports (sport_code), 
	CONSTRAINT picks_direction_check CHECK (direction = ANY (ARRAY['over'::text, 'under'::text])), 
	CONSTRAINT picks_leg_result_check CHECK (leg_result = ANY (ARRAY['win'::text, 'loss'::text, 'push'::text, 'void'::text])), 
	CONSTRAINT picks_parlay_result_check CHECK (parlay_result = ANY (ARRAY['win'::text, 'loss'::text, 'push'::text, 'void'::text]))
);
CREATE INDEX idx_picks_date ON public.picks (picked_at);
CREATE INDEX idx_picks_group ON public.picks (pick_group_id);
CREATE INDEX idx_picks_sport ON public.picks (sport_code, picked_at);
CREATE INDEX idx_picks_unsettled ON public.picks (game_id) WHERE (leg_result IS NULL);
CREATE UNIQUE INDEX picks_unique_per_day ON public.picks (player_id, line_id, ((picked_at AT TIME ZONE 'America/Los_Angeles'::text)::date));

