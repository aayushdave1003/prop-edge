"""Train NHL game winner model: win probability + implied goal spread.

Two LightGBM models trained on ~1,451 completed NHL games (2025-26 regular
season):
  - Classifier  → P(home team wins)
  - Regressor   → expected home goal margin (implied puck line)

Features (per team, rolling, lookahead-safe — shift before rolling):
  goals for/against, shots for/against, shooting % (goals/shots),
  save % (1 - goals_allowed/shots_against), goal margin, win rate, days rest,
  back-to-back, games played this season, playoffs flag. Built one row per
  (team, game), then pivoted to home_*/away_* per game.

Hockey reality check: low-scoring, high-variance — single-goal games decided in
OT/shootout are near coin-flips, so a realistic AUC is ~0.55-0.62 and accuracy
may only just beat the ~52% home-win baseline.

Train/test split: chronological. Earliest ~80% of games (by date) train, latest
~20% test. The number of boosting rounds is chosen by k-fold `lgb.cv` on the
training set (NOT a single time-tail val window, and NOT even a single random
val split): on one short season a single val holdout is a lottery — some splits
collapse to iter 1 (null model), the WNBA-build failure mode. 5-fold CV averages
that noise away and reliably picks a non-degenerate round count; the final model
is then refit on ALL training data. The TEST set stays a clean chrono holdout.

Run against prod (read-only):
  DATABASE_URL=$(grep '^RAILWAY_DATABASE_URL=' .env | cut -d= -f2- | tr -d '"'"'\"' ) \
    python -m props.models.nhl_winner_v1
"""
import json
from datetime import date
from pathlib import Path
import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import roc_auc_score, log_loss

from props.utils.db import engine, db_banner
from props.utils.logging import log, configure_logging


MODEL_DIR  = Path("models")
MODEL_DIR.mkdir(exist_ok=True)
CLF_PATH   = MODEL_DIR / "nhl_winner_v1_classifier.txt"
REG_PATH   = MODEL_DIR / "nhl_winner_v1_regressor.txt"
META_PATH  = MODEL_DIR / "nhl_winner_v1_meta.json"

WINDOWS = [5, 10, 20]

TEAM_FEATURES = [
    *(f"last_{w}_avg_goals_scored"   for w in WINDOWS),
    "season_avg_goals_scored",
    *(f"last_{w}_avg_goals_allowed"  for w in WINDOWS),
    "season_avg_goals_allowed",
    *(f"last_{w}_avg_margin"         for w in [5, 10]),
    "season_avg_margin",
    *(f"last_{w}_win_rate"           for w in [5, 10]),
    "season_win_rate",
    "last_10_avg_shots_for",
    "season_avg_shots_for",
    "last_10_avg_shots_against",
    "season_avg_shots_against",
    "last_5_avg_shooting_pct",
    "last_10_avg_shooting_pct",
    "last_5_avg_save_pct",
    "last_10_avg_save_pct",
    "days_rest",
    "is_back_to_back",
    "games_played_season",
    "is_playoffs",
]

GAME_FEATURE_KEYS = (
    [f"home_{f}" for f in TEAM_FEATURES] +
    [f"away_{f}" for f in TEAM_FEATURES]
)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_team_game_stats() -> pd.DataFrame:
    """One row per (team, game): goals scored/allowed, win, shots for/against.

    `goals` and `shots` are stored per-skater as clean numerics, so they sum
    to the team total (summed goals == the game score, verified). The goalie
    fields (saves/save_pct/shots_against) are stored as strings like "22/25" and
    are NOT used; save % is derived instead from the opponent's goals vs shots.
    No `home_score > 0` filter — a 0 is a legitimate hockey score (shutout).
    """
    log.info("loading_team_game_stats")
    sql = """
        WITH team_stat AS (
            SELECT pg.game_id, pg.team_id,
                   SUM((pg.stats->>'goals')::float) AS goals,
                   SUM((pg.stats->>'shots')::float) AS shots
            FROM player_games pg
            JOIN games g USING (game_id)
            WHERE g.sport_code = 'nhl'
              AND g.status = 'final'
              AND g.home_score IS NOT NULL
            GROUP BY pg.game_id, pg.team_id
        )
        SELECT
            g.game_id, g.game_date, g.season, g.season_type,
            pg.team_id, pg.is_home,
            my.goals  AS goals_scored,
            opp.goals AS goals_allowed,
            my.shots  AS shots_for,
            opp.shots AS shots_against,
            CASE WHEN my.goals > opp.goals THEN 1 ELSE 0 END AS won,
            CASE WHEN my.shots > 0  THEN my.goals  / my.shots  ELSE 0 END AS shooting_pct,
            CASE WHEN opp.shots > 0 THEN 1.0 - (opp.goals / opp.shots) ELSE 1 END AS save_pct
        FROM player_games pg
        JOIN games g USING (game_id)
        JOIN team_stat my  ON my.game_id  = pg.game_id AND my.team_id  = pg.team_id
        JOIN team_stat opp ON opp.game_id = pg.game_id AND opp.team_id = pg.opponent_id
        WHERE g.sport_code = 'nhl'
          AND g.status = 'final'
          AND g.home_score IS NOT NULL
          AND pg.team_id <> pg.opponent_id
        GROUP BY g.game_id, g.game_date, g.season, g.season_type,
                 pg.team_id, pg.is_home, my.goals, opp.goals, my.shots, opp.shots
        ORDER BY pg.team_id, g.game_date, g.game_id
    """
    df = pd.read_sql(sql, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    df["margin"]    = df["goals_scored"] - df["goals_allowed"]
    df["is_playoffs"] = (df["season_type"].isin(["playoffs", "play_in"])).astype(int)
    log.info("team_game_rows", n=len(df))
    return df


def load_game_results() -> pd.DataFrame:
    """One row per game with home/away goal totals (= scores)."""
    sql = """
        SELECT game_id, game_date, season, season_type,
               home_team_id, away_team_id,
               home_score, away_score,
               home_score - away_score AS home_margin,
               CASE WHEN home_score > away_score THEN 1 ELSE 0 END AS home_wins
        FROM games
        WHERE sport_code = 'nhl'
          AND status = 'final'
          AND home_score IS NOT NULL
          AND home_team_id <> away_team_id
        ORDER BY game_date, game_id
    """
    df = pd.read_sql(sql, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    return df


# ---------------------------------------------------------------------------
# Feature computation
# ---------------------------------------------------------------------------

def compute_team_rolling(team_df: pd.DataFrame) -> pd.DataFrame:
    """For each (team, game), compute rolling features from prior games only."""
    log.info("computing_team_rolling_features")
    results = []

    for team_id, grp in team_df.groupby("team_id"):
        g = grp.sort_values(["game_date", "game_id"]).reset_index(drop=True)
        feats = {"team_id": [], "game_id": []}

        prior_goals_scored  = g["goals_scored"].shift(1)
        prior_goals_allowed = g["goals_allowed"].shift(1)
        prior_margin        = g["margin"].shift(1)
        prior_won           = g["won"].shift(1)
        prior_shots_for     = g["shots_for"].shift(1)
        prior_shots_against = g["shots_against"].shift(1)
        prior_shoot_pct     = g["shooting_pct"].shift(1)
        prior_save_pct      = g["save_pct"].shift(1)

        # Days rest
        prior_dates  = g["game_date"].shift(1)
        days_rest    = (g["game_date"] - prior_dates).dt.days.fillna(3).clip(1, 14)
        is_b2b       = (days_rest == 1).astype(int)

        # Season game count (prior games this season)
        season_marker    = (g["season"] != g["season"].shift(1)).cumsum()
        games_played     = g.groupby(season_marker).cumcount()

        feats["team_id"]  = g["team_id"].values
        feats["game_id"]  = g["game_id"].values
        feats["days_rest"]           = days_rest.values
        feats["is_back_to_back"]     = is_b2b.values
        feats["games_played_season"] = games_played.values
        feats["is_playoffs"]         = g["is_playoffs"].values

        for w in WINDOWS:
            feats[f"last_{w}_avg_goals_scored"]  = prior_goals_scored.rolling(w, min_periods=1).mean().fillna(0).values
            feats[f"last_{w}_avg_goals_allowed"] = prior_goals_allowed.rolling(w, min_periods=1).mean().fillna(0).values
        for w in [5, 10]:
            feats[f"last_{w}_avg_margin"]  = prior_margin.rolling(w, min_periods=1).mean().fillna(0).values
            feats[f"last_{w}_win_rate"]    = prior_won.rolling(w, min_periods=1).mean().fillna(0.5).values

        for stat in ["goals_scored", "goals_allowed", "margin",
                     "shots_for", "shots_against"]:
            season_avg = g.groupby(season_marker)[stat].apply(
                lambda s: s.shift(1).expanding().mean()
            ).reset_index(level=0, drop=True).fillna(0)
            feats[f"season_avg_{stat}"] = season_avg.values

        feats["last_10_avg_shots_for"]     = prior_shots_for.rolling(10, min_periods=1).mean().fillna(0).values
        feats["last_10_avg_shots_against"] = prior_shots_against.rolling(10, min_periods=1).mean().fillna(0).values
        feats["last_5_avg_shooting_pct"]   = prior_shoot_pct.rolling(5,  min_periods=1).mean().fillna(0).values
        feats["last_10_avg_shooting_pct"]  = prior_shoot_pct.rolling(10, min_periods=1).mean().fillna(0).values
        feats["last_5_avg_save_pct"]       = prior_save_pct.rolling(5,  min_periods=1).mean().fillna(0.9).values
        feats["last_10_avg_save_pct"]      = prior_save_pct.rolling(10, min_periods=1).mean().fillna(0.9).values

        feats["season_win_rate"] = g.groupby(season_marker)["won"].apply(
            lambda s: s.shift(1).expanding().mean()
        ).reset_index(level=0, drop=True).fillna(0.5).values

        results.append(pd.DataFrame(feats))

    out = pd.concat(results, ignore_index=True)
    log.info("team_rolling_done", rows=len(out))
    return out


def build_game_features(games_df: pd.DataFrame, team_features: pd.DataFrame) -> pd.DataFrame:
    """Pivot team features into one row per game: home_* and away_* columns."""
    tf = team_features.set_index(["team_id", "game_id"])
    feat_cols = [c for c in team_features.columns if c not in ("team_id", "game_id")]

    rows = []
    for _, game in games_df.iterrows():
        gid   = game["game_id"]
        htid  = game["home_team_id"]
        atid  = game["away_team_id"]

        try:
            hf = tf.loc[(htid, gid)]
            af = tf.loc[(atid, gid)]
        except KeyError:
            continue

        row = {"game_id": gid, "game_date": game["game_date"],
               "home_wins": game["home_wins"], "home_margin": game["home_margin"]}
        for f in feat_cols:
            row[f"home_{f}"] = float(hf[f]) if f in hf.index else 0.0
            row[f"away_{f}"] = float(af[f]) if f in af.index else 0.0
        rows.append(row)

    out = pd.DataFrame(rows)
    log.info("game_feature_rows", n=len(out))
    return out


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

CLF_PARAMS = {
    "objective": "binary", "metric": "auc",
    "learning_rate": 0.03, "num_leaves": 15, "min_data_in_leaf": 30,
    "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 5,
    "verbose": -1, "seed": 42,
}
REG_PARAMS = {
    "objective": "regression", "metric": "mae",
    "learning_rate": 0.03, "num_leaves": 15, "min_data_in_leaf": 30,
    "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 5,
    "verbose": -1, "seed": 42,
}
CV_FOLDS  = 5
CV_SEED   = 42
MAX_ROUNDS = 2000
ES_PATIENCE = 60


def _cv_best_rounds(params, X, y, label) -> int:
    """Pick #boosting rounds by k-fold CV (averages away the single-val-split
    lottery that collapses to iter 1 on one short season). Floor of 5 so the
    final model is never a degenerate stump even if CV is pessimistic."""
    ds = lgb.Dataset(X, y)
    cvr = lgb.cv(
        params, ds, num_boost_round=MAX_ROUNDS, nfold=CV_FOLDS,
        stratified=(params["objective"] == "binary"), shuffle=True, seed=CV_SEED,
        callbacks=[lgb.early_stopping(ES_PATIENCE, verbose=False)],
    )
    mean_key = next(k for k in cvr if k.endswith("-mean"))
    best_rounds = max(len(cvr[mean_key]), 5)
    log.info("cv_rounds", model=label, rounds=best_rounds,
             cv_score=round(float(cvr[mean_key][-1]), 4))
    return best_rounds


def train_classifier(train_df):
    X, y = train_df[GAME_FEATURE_KEYS], train_df["home_wins"]
    rounds = _cv_best_rounds(CLF_PARAMS, X, y, "classifier")
    model = lgb.train(CLF_PARAMS, lgb.Dataset(X, y), num_boost_round=rounds)
    model.best_iteration = rounds
    log.info("classifier_trained", rounds=rounds)
    return model


def train_regressor(train_df):
    X, y = train_df[GAME_FEATURE_KEYS], train_df["home_margin"]
    rounds = _cv_best_rounds(REG_PARAMS, X, y, "regressor")
    model = lgb.train(REG_PARAMS, lgb.Dataset(X, y), num_boost_round=rounds)
    model.best_iteration = rounds
    log.info("regressor_trained", rounds=rounds)
    return model


def evaluate(clf, reg, test_df):
    X_test = test_df[GAME_FEATURE_KEYS]
    y_wins  = test_df["home_wins"].values
    y_margin = test_df["home_margin"].values

    p_win    = clf.predict(X_test, num_iteration=clf.best_iteration)
    pred_margin = reg.predict(X_test, num_iteration=reg.best_iteration)

    auc   = roc_auc_score(y_wins, p_win)
    ll    = log_loss(y_wins, p_win)
    acc   = ((p_win > 0.5) == y_wins).mean()
    mae   = np.mean(np.abs(pred_margin - y_margin))
    rmse  = np.sqrt(np.mean((pred_margin - y_margin) ** 2))
    base_acc  = max(y_wins.mean(), 1 - y_wins.mean())

    log.info("test_metrics",
             auc=round(auc, 4), log_loss=round(ll, 4),
             accuracy=round(acc, 4), baseline_acc=round(base_acc, 4),
             margin_mae=round(mae, 2), margin_rmse=round(rmse, 2))

    print(f"\n=== Winner model test results (n={len(test_df)}) ===")
    print(f"  Win prediction:  AUC={auc:.3f}  Acc={acc:.1%}  (baseline={base_acc:.1%})")
    print(f"  Margin (spread): MAE={mae:.2f} goals  RMSE={rmse:.2f} goals")

    print("\n=== Feature importance (top 15) ===")
    imp = pd.DataFrame({
        "feature": clf.feature_name(),
        "importance": clf.feature_importance("gain"),
    }).sort_values("importance", ascending=False)
    print(imp.head(15).to_string(index=False))

    return {"auc": auc, "accuracy": acc, "baseline_acc": base_acc,
            "margin_mae": mae, "margin_rmse": rmse, "log_loss": ll}


def main():
    configure_logging()
    print(db_banner())

    team_df  = load_team_game_stats()
    games_df = load_game_results()

    team_feats  = compute_team_rolling(team_df)
    game_feats  = build_game_features(games_df, team_feats)

    # NHL data is all one block (2025-26 regular season). Chronological split:
    # earliest ~80% by date for training, latest ~20% as a clean holdout. The
    # round count is chosen by k-fold CV inside train_* (see module docstring),
    # so there is no separate val split to leak.
    cutoff = game_feats["game_date"].quantile(0.8)
    train_all = game_feats[game_feats["game_date"] < cutoff].copy()
    test_df   = game_feats[game_feats["game_date"] >= cutoff].copy()

    log.info("split", cutoff=str(cutoff.date()),
             train=len(train_all), test=len(test_df))

    clf = train_classifier(train_all)
    reg = train_regressor(train_all)

    metrics = evaluate(clf, reg, test_df)

    clf.save_model(str(CLF_PATH))
    reg.save_model(str(REG_PATH))

    meta = {
        "classifier_path": str(CLF_PATH),
        "regressor_path":  str(REG_PATH),
        "feature_keys":    GAME_FEATURE_KEYS,
        "team_features":   TEAM_FEATURES,
        "split_cutoff":    str(cutoff.date()),
        "clf_rounds":      clf.best_iteration,
        "reg_rounds":      reg.best_iteration,
        "cv_folds":        CV_FOLDS,
        "train_n": len(train_all), "test_n": len(test_df),
        "test_auc":          metrics["auc"],
        "test_accuracy":     metrics["accuracy"],
        "baseline_accuracy": metrics["baseline_acc"],
        "test_log_loss":     metrics["log_loss"],
        "margin_mae":        metrics["margin_mae"],
        "margin_rmse":       metrics["margin_rmse"],
        "trained_date":      date.today().isoformat(),
    }
    with open(META_PATH, "w") as f:
        json.dump(meta, f, indent=2)

    log.info("winner_model_saved", clf=str(CLF_PATH), reg=str(REG_PATH))


if __name__ == "__main__":
    main()
