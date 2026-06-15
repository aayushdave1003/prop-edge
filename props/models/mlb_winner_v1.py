"""Train MLB game winner model: win probability + implied run line.

Two LightGBM models trained on completed MLB games:
  - Classifier  → P(home team wins)
  - Regressor   → expected home run margin (implied run line)

Features:
  - Team offense: rolling avg runs, hits, HR, BB, K allowed
  - Team defense: rolling avg runs allowed
  - Starting pitcher: rolling ERA, WHIP, K/9 (from outs_recorded >= 15)
  - Context: days_rest, is_back_to_back, games_played_season

Train/test split: games before 2026-01-01 for training, Jan 2026+ for test.
"""
import json
from datetime import date
from pathlib import Path
import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import roc_auc_score, log_loss

from props.utils.db import engine
from props.utils.logging import log, configure_logging


MODEL_DIR  = Path("models")
MODEL_DIR.mkdir(exist_ok=True)
CLF_PATH   = MODEL_DIR / "mlb_winner_v1_classifier.txt"
REG_PATH   = MODEL_DIR / "mlb_winner_v1_regressor.txt"
META_PATH  = MODEL_DIR / "mlb_winner_v1_meta.json"

WINDOWS = [5, 10, 20]

TEAM_FEATURES = [
    *(f"last_{w}_avg_runs_scored"   for w in WINDOWS),
    "season_avg_runs_scored",
    *(f"last_{w}_avg_runs_allowed"  for w in WINDOWS),
    "season_avg_runs_allowed",
    *(f"last_{w}_avg_margin"        for w in [5, 10]),
    "season_avg_margin",
    *(f"last_{w}_win_rate"          for w in [5, 10]),
    "season_win_rate",
    "last_10_avg_hits_scored",
    "last_10_avg_hr_scored",
    "last_10_avg_bb_scored",
    "days_rest",
    "is_back_to_back",
    "games_played_season",
]

PITCHER_FEATURES = [
    *(f"sp_last_{w}_era"   for w in [3, 5, 10]),
    *(f"sp_last_{w}_whip"  for w in [3, 5, 10]),
    *(f"sp_last_{w}_k9"    for w in [3, 5, 10]),
    "sp_season_era",
    "sp_season_whip",
    "sp_season_k9",
    "sp_games_started",
]

GAME_FEATURE_KEYS = (
    [f"home_{f}" for f in TEAM_FEATURES] +
    [f"away_{f}" for f in TEAM_FEATURES] +
    [f"home_{f}" for f in PITCHER_FEATURES] +
    [f"away_{f}" for f in PITCHER_FEATURES]
)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_team_game_stats() -> pd.DataFrame:
    """One row per (team, game): runs scored/allowed, win, batting stats."""
    log.info("loading_mlb_team_game_stats")
    sql = """
        WITH team_runs AS (
            SELECT
                pg.game_id, pg.team_id,
                SUM(COALESCE((pg.stats->>'runs')::float, 0))       AS runs,
                SUM(COALESCE((pg.stats->>'hits')::float, 0))       AS hits,
                SUM(COALESCE((pg.stats->>'home_runs')::float, 0))  AS hr,
                SUM(COALESCE((pg.stats->>'walks')::float, 0))      AS bb
            FROM player_games pg
            JOIN games g USING(game_id)
            WHERE g.sport_code = 'mlb'
              AND g.status = 'final'
            GROUP BY pg.game_id, pg.team_id
            HAVING SUM(COALESCE((pg.stats->>'runs')::float, 0)) >= 0
        )
        SELECT
            g.game_id, g.game_date, g.season, g.season_type,
            my.team_id,
            CASE WHEN g.home_team_id = my.team_id THEN 1 ELSE 0 END AS is_home,
            my.runs  AS runs_scored,
            opp.runs AS runs_allowed,
            my.hits  AS hits,
            my.hr    AS hr,
            my.bb    AS bb,
            CASE WHEN my.runs > opp.runs THEN 1 ELSE 0 END AS won
        FROM team_runs my
        JOIN games g ON g.game_id = my.game_id
        JOIN team_runs opp ON opp.game_id = my.game_id
            AND opp.team_id = CASE
                WHEN g.home_team_id = my.team_id THEN g.away_team_id
                ELSE g.home_team_id
            END
        WHERE g.sport_code = 'mlb'
          AND g.status = 'final'
          AND (g.home_team_id = my.team_id OR g.away_team_id = my.team_id)
          AND my.runs + opp.runs > 0
        ORDER BY my.team_id, g.game_date, g.game_id
    """
    df = pd.read_sql(sql, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    df["margin"] = df["runs_scored"] - df["runs_allowed"]
    df["is_playoffs"] = (df["season_type"].isin(["playoffs", "play_in"])).astype(int)
    log.info("mlb_team_game_rows", n=len(df))
    return df


def load_starting_pitcher_stats() -> pd.DataFrame:
    """One row per (game, team): starting pitcher stats (player with most outs_recorded)."""
    log.info("loading_mlb_starter_stats")
    sql = """
        WITH ranked AS (
            SELECT
                pg.game_id, pg.team_id, pg.player_id,
                (pg.stats->>'outs_recorded')::float   AS outs,
                (pg.stats->>'earned_runs')::float     AS er,
                (pg.stats->>'hits_allowed')::float    AS h_allowed,
                (pg.stats->>'walks_allowed')::float   AS bb_allowed,
                (pg.stats->>'strikeouts_pitcher')::float AS k,
                (pg.stats->>'batters_faced')::float   AS bf,
                ROW_NUMBER() OVER (
                    PARTITION BY pg.game_id, pg.team_id
                    ORDER BY (pg.stats->>'outs_recorded')::float DESC
                ) AS rn
            FROM player_games pg
            JOIN games g USING(game_id)
            WHERE g.sport_code = 'mlb'
              AND g.status = 'final'
              AND (pg.stats->>'outs_recorded')::float >= 9
        )
        SELECT
            r.game_id, r.team_id, r.player_id,
            r.outs, r.er, r.h_allowed, r.bb_allowed, r.k, r.bf,
            g.game_date
        FROM ranked r
        JOIN games g USING(game_id)
        WHERE r.rn = 1
        ORDER BY r.player_id, g.game_date, r.game_id
    """
    df = pd.read_sql(sql, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    df["ip"] = df["outs"] / 3.0
    df["era_game"]  = (df["er"] / df["ip"].clip(lower=0.33)) * 9
    df["whip_game"] = (df["h_allowed"] + df["bb_allowed"]) / df["ip"].clip(lower=0.33)
    df["k9_game"]   = (df["k"] / df["ip"].clip(lower=0.33)) * 9
    log.info("starter_rows", n=len(df))
    return df


def load_game_results() -> pd.DataFrame:
    """One row per game with home/away runs (derived from player_games since games.home_score is NULL for MLB)."""
    sql = """
        WITH team_runs AS (
            SELECT pg.game_id, pg.team_id,
                   SUM(COALESCE((pg.stats->>'runs')::float, 0)) AS runs
            FROM player_games pg
            JOIN games g USING(game_id)
            WHERE g.sport_code = 'mlb' AND g.status = 'final'
            GROUP BY pg.game_id, pg.team_id
        )
        SELECT
            g.game_id, g.game_date, g.season, g.season_type,
            g.home_team_id, g.away_team_id,
            hr.runs  AS home_score,
            ar.runs  AS away_score,
            hr.runs - ar.runs AS home_margin,
            CASE WHEN hr.runs > ar.runs THEN 1 ELSE 0 END AS home_wins
        FROM games g
        JOIN team_runs hr ON hr.game_id = g.game_id AND hr.team_id = g.home_team_id
        JOIN team_runs ar ON ar.game_id = g.game_id AND ar.team_id = g.away_team_id
        WHERE g.sport_code = 'mlb'
          AND g.status = 'final'
          AND g.home_team_id <> g.away_team_id
          AND hr.runs + ar.runs > 0
        ORDER BY g.game_date, g.game_id
    """
    df = pd.read_sql(sql, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    return df


# ---------------------------------------------------------------------------
# Feature computation
# ---------------------------------------------------------------------------

def compute_team_rolling(team_df: pd.DataFrame) -> pd.DataFrame:
    """For each (team, game), compute rolling features from prior games only."""
    log.info("computing_mlb_team_rolling")
    results = []

    for team_id, grp in team_df.groupby("team_id"):
        g = grp.sort_values(["game_date", "game_id"]).reset_index(drop=True)

        prior_runs_scored  = g["runs_scored"].shift(1)
        prior_runs_allowed = g["runs_allowed"].shift(1)
        prior_margin       = g["margin"].shift(1)
        prior_won          = g["won"].shift(1)
        prior_hits         = g["hits"].shift(1)
        prior_hr           = g["hr"].shift(1)
        prior_bb           = g["bb"].shift(1)

        prior_dates = g["game_date"].shift(1)
        days_rest   = (g["game_date"] - prior_dates).dt.days.fillna(1).clip(1, 7)
        is_b2b      = (days_rest == 1).astype(int)

        season_marker    = (g["season"] != g["season"].shift(1)).cumsum()
        games_played     = g.groupby(season_marker).cumcount()

        feats = {
            "team_id": g["team_id"].values,
            "game_id": g["game_id"].values,
            "days_rest":           days_rest.values,
            "is_back_to_back":     is_b2b.values,
            "games_played_season": games_played.values,
        }

        for w in WINDOWS:
            feats[f"last_{w}_avg_runs_scored"]  = prior_runs_scored.rolling(w, min_periods=1).mean().fillna(0).values
            feats[f"last_{w}_avg_runs_allowed"] = prior_runs_allowed.rolling(w, min_periods=1).mean().fillna(0).values

        for w in [5, 10]:
            feats[f"last_{w}_avg_margin"]  = prior_margin.rolling(w, min_periods=1).mean().fillna(0).values
            feats[f"last_{w}_win_rate"]    = prior_won.rolling(w, min_periods=1).mean().fillna(0.5).values

        feats["last_10_avg_hits_scored"] = prior_hits.rolling(10, min_periods=1).mean().fillna(0).values
        feats["last_10_avg_hr_scored"]   = prior_hr.rolling(10, min_periods=1).mean().fillna(0).values
        feats["last_10_avg_bb_scored"]   = prior_bb.rolling(10, min_periods=1).mean().fillna(0).values

        for stat, col in [("runs_scored", prior_runs_scored), ("runs_allowed", prior_runs_allowed),
                           ("margin", prior_margin)]:
            season_avg = g.groupby(season_marker)[stat].apply(
                lambda s: s.shift(1).expanding().mean()
            ).reset_index(level=0, drop=True).fillna(0)
            feats[f"season_avg_{stat}"] = season_avg.values

        feats["season_win_rate"] = g.groupby(season_marker)["won"].apply(
            lambda s: s.shift(1).expanding().mean()
        ).reset_index(level=0, drop=True).fillna(0.5).values

        results.append(pd.DataFrame(feats))

    out = pd.concat(results, ignore_index=True)
    log.info("mlb_team_rolling_done", rows=len(out))
    return out


def compute_pitcher_rolling(pitcher_df: pd.DataFrame) -> pd.DataFrame:
    """For each starter appearance, compute rolling ERA/WHIP/K9 from prior starts."""
    log.info("computing_pitcher_rolling")
    results = []

    for pid, grp in pitcher_df.groupby("player_id"):
        g = grp.sort_values(["game_date", "game_id"]).reset_index(drop=True)
        if len(g) < 2:
            continue

        prior_era  = g["era_game"].shift(1)
        prior_whip = g["whip_game"].shift(1)
        prior_k9   = g["k9_game"].shift(1)

        feats = {
            "player_id": g["player_id"].values,
            "game_id":   g["game_id"].values,
            "team_id":   g["team_id"].values,
        }

        for w in [3, 5, 10]:
            feats[f"sp_last_{w}_era"]  = prior_era.rolling(w, min_periods=1).mean().fillna(4.5).values
            feats[f"sp_last_{w}_whip"] = prior_whip.rolling(w, min_periods=1).mean().fillna(1.35).values
            feats[f"sp_last_{w}_k9"]   = prior_k9.rolling(w, min_periods=1).mean().fillna(8.0).values

        feats["sp_season_era"]  = prior_era.expanding().mean().fillna(4.5).values
        feats["sp_season_whip"] = prior_whip.expanding().mean().fillna(1.35).values
        feats["sp_season_k9"]   = prior_k9.expanding().mean().fillna(8.0).values
        feats["sp_games_started"] = pd.Series(range(len(g))).values

        results.append(pd.DataFrame(feats))

    out = pd.concat(results, ignore_index=True)
    log.info("pitcher_rolling_done", rows=len(out))
    return out


def build_game_features(
    games_df: pd.DataFrame,
    team_features: pd.DataFrame,
    pitcher_features: pd.DataFrame,
) -> pd.DataFrame:
    """Pivot features into one row per game: home_* and away_* columns."""
    tf = team_features.set_index(["team_id", "game_id"])
    team_feat_cols = [c for c in team_features.columns if c not in ("team_id", "game_id")]

    # pitcher lookup: (team_id, game_id) → pitcher rolling features
    pf_idx = pitcher_features.set_index(["team_id", "game_id"])
    pit_feat_cols = [c for c in pitcher_features.columns if c not in ("team_id", "game_id", "player_id")]

    DEFAULT_PIT = {f"sp_last_{w}_era": 4.5 for w in [3,5,10]}
    DEFAULT_PIT.update({f"sp_last_{w}_whip": 1.35 for w in [3,5,10]})
    DEFAULT_PIT.update({f"sp_last_{w}_k9": 8.0 for w in [3,5,10]})
    DEFAULT_PIT.update({"sp_season_era": 4.5, "sp_season_whip": 1.35,
                        "sp_season_k9": 8.0, "sp_games_started": 0})

    rows = []
    for _, game in games_df.iterrows():
        gid  = game["game_id"]
        htid = game["home_team_id"]
        atid = game["away_team_id"]

        try:
            hf = tf.loc[(htid, gid)]
            af = tf.loc[(atid, gid)]
        except KeyError:
            continue

        row = {
            "game_id": gid,
            "game_date": game["game_date"],
            "home_wins": game["home_wins"],
            "home_margin": game["home_margin"],
        }

        for f in team_feat_cols:
            row[f"home_{f}"] = float(hf[f]) if f in hf.index else 0.0
            row[f"away_{f}"] = float(af[f]) if f in af.index else 0.0

        # Pitcher features — use defaults if no qualifying starter found
        try:
            hpf = pf_idx.loc[(htid, gid)]
        except KeyError:
            hpf = DEFAULT_PIT
        try:
            apf = pf_idx.loc[(atid, gid)]
        except KeyError:
            apf = DEFAULT_PIT

        for f in pit_feat_cols:
            row[f"home_{f}"] = float(hpf[f]) if hasattr(hpf, "__getitem__") and f in (hpf.index if hasattr(hpf, "index") else hpf) else DEFAULT_PIT.get(f, 0.0)
            row[f"away_{f}"] = float(apf[f]) if hasattr(apf, "__getitem__") and f in (apf.index if hasattr(apf, "index") else apf) else DEFAULT_PIT.get(f, 0.0)

        rows.append(row)

    out = pd.DataFrame(rows)
    log.info("mlb_game_feature_rows", n=len(out))
    return out


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train_classifier(train_df, val_df):
    X_tr, y_tr = train_df[GAME_FEATURE_KEYS], train_df["home_wins"]
    X_vl, y_vl = val_df[GAME_FEATURE_KEYS],   val_df["home_wins"]
    ds_tr = lgb.Dataset(X_tr, y_tr)
    ds_vl = lgb.Dataset(X_vl, y_vl, reference=ds_tr)
    params = {
        "objective": "binary", "metric": ["binary_logloss", "auc"],
        "learning_rate": 0.04, "num_leaves": 20, "min_data_in_leaf": 30,
        "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 5,
        "verbose": -1, "seed": 42,
    }
    model = lgb.train(
        params, ds_tr, num_boost_round=1000, valid_sets=[ds_tr, ds_vl],
        valid_names=["train", "val"],
        callbacks=[lgb.early_stopping(50), lgb.log_evaluation(200)],
    )
    log.info("mlb_classifier_trained", best_iter=model.best_iteration)
    return model


def train_regressor(train_df, val_df):
    X_tr, y_tr = train_df[GAME_FEATURE_KEYS], train_df["home_margin"]
    X_vl, y_vl = val_df[GAME_FEATURE_KEYS],   val_df["home_margin"]
    ds_tr = lgb.Dataset(X_tr, y_tr)
    ds_vl = lgb.Dataset(X_vl, y_vl, reference=ds_tr)
    params = {
        "objective": "regression", "metric": ["mae", "rmse"],
        "learning_rate": 0.04, "num_leaves": 20, "min_data_in_leaf": 30,
        "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 5,
        "verbose": -1, "seed": 42,
    }
    model = lgb.train(
        params, ds_tr, num_boost_round=1000, valid_sets=[ds_tr, ds_vl],
        valid_names=["train", "val"],
        callbacks=[lgb.early_stopping(50), lgb.log_evaluation(200)],
    )
    log.info("mlb_regressor_trained", best_iter=model.best_iteration)
    return model


def evaluate(clf, reg, test_df):
    X_test  = test_df[GAME_FEATURE_KEYS]
    y_wins  = test_df["home_wins"].values
    y_margin = test_df["home_margin"].values

    p_win        = clf.predict(X_test, num_iteration=clf.best_iteration)
    pred_margin  = reg.predict(X_test, num_iteration=reg.best_iteration)

    auc   = roc_auc_score(y_wins, p_win)
    ll    = log_loss(y_wins, p_win)
    acc   = ((p_win > 0.5) == y_wins).mean()
    mae   = np.mean(np.abs(pred_margin - y_margin))
    rmse  = np.sqrt(np.mean((pred_margin - y_margin) ** 2))
    base_acc = max(y_wins.mean(), 1 - y_wins.mean())

    log.info("mlb_test_metrics",
             auc=round(auc, 4), log_loss=round(ll, 4),
             accuracy=round(acc, 4), baseline_acc=round(base_acc, 4),
             margin_mae=round(mae, 2), margin_rmse=round(rmse, 2))

    print(f"\n=== MLB Winner model test results (n={len(test_df)}) ===")
    print(f"  Win prediction:  AUC={auc:.3f}  Acc={acc:.1%}  (baseline={base_acc:.1%})")
    print(f"  Margin (run line): MAE={mae:.1f}  RMSE={rmse:.1f}")

    print("\n=== Feature importance (top 15) ===")
    imp = pd.DataFrame({
        "feature": clf.feature_name(),
        "importance": clf.feature_importance("gain"),
    }).sort_values("importance", ascending=False)
    print(imp.head(15).to_string(index=False))

    return {"auc": auc, "accuracy": acc, "margin_mae": mae}


def main():
    configure_logging()

    team_df    = load_team_game_stats()
    pitcher_df = load_starting_pitcher_stats()
    games_df   = load_game_results()

    team_feats    = compute_team_rolling(team_df)
    pitcher_feats = compute_pitcher_rolling(pitcher_df)
    game_feats    = build_game_features(games_df, team_feats, pitcher_feats)

    if len(game_feats) == 0:
        log.error("no_game_features_built")
        return

    cutoff = pd.Timestamp("2026-01-01")
    train_all = game_feats[game_feats["game_date"] < cutoff].copy()
    test_df   = game_feats[game_feats["game_date"] >= cutoff].copy()

    if len(train_all) < 100:
        log.error("insufficient_training_data", n=len(train_all))
        return

    # Row-based split avoids hitting MLB offseason dead zones with tiny val sets
    fit_n  = int(len(train_all) * 0.85)
    fit_df = train_all.iloc[:fit_n]
    val_df = train_all.iloc[fit_n:]

    log.info("mlb_split", fit=len(fit_df), val=len(val_df), test=len(test_df))

    clf = train_classifier(fit_df, val_df)
    reg = train_regressor(fit_df, val_df)

    metrics = evaluate(clf, reg, test_df)

    clf.save_model(str(CLF_PATH))
    reg.save_model(str(REG_PATH))

    meta = {
        "classifier_path": str(CLF_PATH),
        "regressor_path":  str(REG_PATH),
        "feature_keys":    GAME_FEATURE_KEYS,
        "team_features":   TEAM_FEATURES,
        "pitcher_features": PITCHER_FEATURES,
        "clf_best_iter":   clf.best_iteration,
        "reg_best_iter":   reg.best_iteration,
        "train_n": len(fit_df), "val_n": len(val_df), "test_n": len(test_df),
        "test_auc":      metrics["auc"],
        "test_accuracy": metrics["accuracy"],
        "margin_mae":    metrics["margin_mae"],
        "trained_date":  date.today().isoformat(),
    }
    with open(META_PATH, "w") as f:
        json.dump(meta, f, indent=2)

    log.info("mlb_winner_model_saved", clf=str(CLF_PATH), reg=str(REG_PATH))
    print(f"\nModels saved to {CLF_PATH} and {REG_PATH}")


if __name__ == "__main__":
    main()
