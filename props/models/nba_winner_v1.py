"""Train NBA game winner model: win probability + implied point spread.

Two LightGBM models trained on 1,372 completed NBA games:
  - Classifier  → P(home team wins)
  - Regressor   → expected home margin (implied spread)

Features: rolling team offense, defense, margin, win rate, pace, rest
for both home and away teams. Lookahead-safe (shift before rolling).

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
CLF_PATH   = MODEL_DIR / "nba_winner_v1_classifier.txt"
REG_PATH   = MODEL_DIR / "nba_winner_v1_regressor.txt"
META_PATH  = MODEL_DIR / "nba_winner_v1_meta.json"

WINDOWS = [5, 10, 20]

TEAM_FEATURES = [
    *(f"last_{w}_avg_pts_scored"   for w in WINDOWS),
    "season_avg_pts_scored",
    *(f"last_{w}_avg_pts_allowed"  for w in WINDOWS),
    "season_avg_pts_allowed",
    *(f"last_{w}_avg_margin"       for w in [5, 10]),
    "season_avg_margin",
    *(f"last_{w}_win_rate"         for w in [5, 10]),
    "season_win_rate",
    "last_10_avg_possessions",
    "season_avg_possessions",
    "last_5_avg_fg_pct",
    "last_10_avg_fg_pct",
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
    """One row per (team, game): pts scored/allowed, win, pace, fg_pct."""
    log.info("loading_team_game_stats")
    sql = """
        WITH team_pts AS (
            SELECT pg.game_id, pg.team_id,
                   SUM((pg.stats->>'points')::float)      AS pts,
                   SUM((pg.stats->>'fg_made')::float)     AS fgm,
                   SUM((pg.stats->>'fg_attempted')::float) AS fga,
                   SUM((pg.stats->>'ft_attempted')::float) AS fta,
                   SUM((pg.stats->>'turnovers')::float)   AS tov,
                   SUM((pg.stats->>'off_rebounds')::float) AS oreb
            FROM player_games pg
            JOIN games g USING (game_id)
            WHERE g.sport_code = 'nba'
              AND g.status = 'final'
              AND g.home_score IS NOT NULL
              AND g.home_score > 0
            GROUP BY pg.game_id, pg.team_id
        )
        SELECT
            g.game_id, g.game_date, g.season, g.season_type,
            pg.team_id, pg.is_home,
            my.pts  AS pts_scored,
            opp.pts AS pts_allowed,
            CASE WHEN my.pts > opp.pts THEN 1 ELSE 0 END AS won,
            COALESCE(my.fga + 0.44*my.fta + my.tov - my.oreb, 0) AS possessions,
            CASE WHEN my.fga > 0 THEN my.fgm / my.fga ELSE 0 END AS fg_pct
        FROM player_games pg
        JOIN games g USING (game_id)
        JOIN team_pts my  ON my.game_id  = pg.game_id AND my.team_id  = pg.team_id
        JOIN team_pts opp ON opp.game_id = pg.game_id AND opp.team_id = pg.opponent_id
        WHERE g.sport_code = 'nba'
          AND g.status = 'final'
          AND g.home_score IS NOT NULL
          AND g.home_score > 0
          AND pg.team_id <> pg.opponent_id
        GROUP BY g.game_id, g.game_date, g.season, g.season_type,
                 pg.team_id, pg.is_home, my.pts, opp.pts, my.fgm, my.fga, my.fta, my.tov, my.oreb
        ORDER BY pg.team_id, g.game_date, g.game_id
    """
    df = pd.read_sql(sql, engine)
    df["game_date"] = pd.to_datetime(df["game_date"])
    df["margin"]    = df["pts_scored"] - df["pts_allowed"]
    df["is_playoffs"] = (df["season_type"].isin(["playoffs", "play_in"])).astype(int)
    log.info("team_game_rows", n=len(df))
    return df


def load_game_results() -> pd.DataFrame:
    """One row per game with home/away scores."""
    sql = """
        SELECT game_id, game_date, season, season_type,
               home_team_id, away_team_id,
               home_score, away_score,
               home_score - away_score AS home_margin,
               CASE WHEN home_score > away_score THEN 1 ELSE 0 END AS home_wins
        FROM games
        WHERE sport_code = 'nba'
          AND status = 'final'
          AND home_score IS NOT NULL
          AND home_score > 0
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

        prior_pts_scored  = g["pts_scored"].shift(1)
        prior_pts_allowed = g["pts_allowed"].shift(1)
        prior_margin      = g["margin"].shift(1)
        prior_won         = g["won"].shift(1)
        prior_poss        = g["possessions"].shift(1)
        prior_fg_pct      = g["fg_pct"].shift(1)

        # Days rest
        prior_dates  = g["game_date"].shift(1)
        days_rest    = (g["game_date"] - prior_dates).dt.days.fillna(3).clip(1, 14)
        is_b2b       = (days_rest == 1).astype(int)

        # Season game count (prior games this season)
        season_marker    = (g["season"] != g["season"].shift(1)).cumsum()
        games_played     = g.groupby(season_marker).cumcount()

        feats["team_id"]  = g["team_id"].values
        feats["game_id"]  = g["game_id"].values
        feats["days_rest"]          = days_rest.values
        feats["is_back_to_back"]    = is_b2b.values
        feats["games_played_season"] = games_played.values
        feats["is_playoffs"]        = g["is_playoffs"].values

        for w in WINDOWS:
            feats[f"last_{w}_avg_pts_scored"]  = prior_pts_scored.rolling(w, min_periods=1).mean().fillna(0).values
            feats[f"last_{w}_avg_pts_allowed"] = prior_pts_allowed.rolling(w, min_periods=1).mean().fillna(0).values
        for w in [5, 10]:
            feats[f"last_{w}_avg_margin"]  = prior_margin.rolling(w, min_periods=1).mean().fillna(0).values
            feats[f"last_{w}_win_rate"]    = prior_won.rolling(w, min_periods=1).mean().fillna(0.5).values

        for stat, col in [("pts_scored", prior_pts_scored), ("pts_allowed", prior_pts_allowed),
                           ("margin", prior_margin), ("possessions", prior_poss), ("fg_pct", prior_fg_pct)]:
            season_avg = g.groupby(season_marker)[stat].apply(
                lambda s: s.shift(1).expanding().mean()
            ).reset_index(level=0, drop=True).fillna(0)
            feats[f"season_avg_{stat}"] = season_avg.values

        feats["last_10_avg_possessions"] = prior_poss.rolling(10, min_periods=1).mean().fillna(0).values
        feats["last_5_avg_fg_pct"]       = prior_fg_pct.rolling(5,  min_periods=1).mean().fillna(0).values
        feats["last_10_avg_fg_pct"]      = prior_fg_pct.rolling(10, min_periods=1).mean().fillna(0).values
        feats["season_win_rate"]         = g.groupby(season_marker)["won"].apply(
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

def train_classifier(train_df, val_df):
    X_tr, y_tr = train_df[GAME_FEATURE_KEYS], train_df["home_wins"]
    X_vl, y_vl = val_df[GAME_FEATURE_KEYS],   val_df["home_wins"]
    ds_tr = lgb.Dataset(X_tr, y_tr)
    ds_vl = lgb.Dataset(X_vl, y_vl, reference=ds_tr)
    params = {
        "objective": "binary", "metric": ["binary_logloss", "auc"],
        "learning_rate": 0.04, "num_leaves": 15, "min_data_in_leaf": 20,
        "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 5,
        "verbose": -1, "seed": 42,
    }
    model = lgb.train(
        params, ds_tr, num_boost_round=1000, valid_sets=[ds_tr, ds_vl],
        valid_names=["train", "val"],
        callbacks=[lgb.early_stopping(50), lgb.log_evaluation(200)],
    )
    log.info("classifier_trained", best_iter=model.best_iteration)
    return model


def train_regressor(train_df, val_df):
    X_tr, y_tr = train_df[GAME_FEATURE_KEYS], train_df["home_margin"]
    X_vl, y_vl = val_df[GAME_FEATURE_KEYS],   val_df["home_margin"]
    ds_tr = lgb.Dataset(X_tr, y_tr)
    ds_vl = lgb.Dataset(X_vl, y_vl, reference=ds_tr)
    params = {
        "objective": "regression", "metric": ["mae", "rmse"],
        "learning_rate": 0.04, "num_leaves": 15, "min_data_in_leaf": 20,
        "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 5,
        "verbose": -1, "seed": 42,
    }
    model = lgb.train(
        params, ds_tr, num_boost_round=1000, valid_sets=[ds_tr, ds_vl],
        valid_names=["train", "val"],
        callbacks=[lgb.early_stopping(50), lgb.log_evaluation(200)],
    )
    log.info("regressor_trained", best_iter=model.best_iteration)
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
    print(f"  Margin (spread): MAE={mae:.1f} pts  RMSE={rmse:.1f} pts")

    print("\n=== Feature importance (top 15) ===")
    imp = pd.DataFrame({
        "feature": clf.feature_name(),
        "importance": clf.feature_importance("gain"),
    }).sort_values("importance", ascending=False)
    print(imp.head(15).to_string(index=False))

    return {"auc": auc, "accuracy": acc, "margin_mae": mae}


def main():
    configure_logging()

    team_df  = load_team_game_stats()
    games_df = load_game_results()

    team_feats  = compute_team_rolling(team_df)
    game_feats  = build_game_features(games_df, team_feats)

    cutoff = pd.Timestamp("2026-01-01")
    train_all = game_feats[game_feats["game_date"] < cutoff].copy()
    test_df   = game_feats[game_feats["game_date"] >= cutoff].copy()

    val_cutoff = train_all["game_date"].max() - pd.Timedelta(days=21)
    fit_df  = train_all[train_all["game_date"] < val_cutoff]
    val_df  = train_all[train_all["game_date"] >= val_cutoff]

    log.info("split", fit=len(fit_df), val=len(val_df), test=len(test_df))

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

    log.info("winner_model_saved", clf=str(CLF_PATH), reg=str(REG_PATH))


if __name__ == "__main__":
    main()
