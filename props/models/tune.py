"""Optuna hyperparameter search for the LightGBM offense models.

Tunes the LGB params on a held-out validation slice (minimising MAE) so the
weekly retrain can try a tuned candidate. The A/B gate still decides whether the
tuned model actually beats prod before anything ships — this just produces a
better candidate to test. Opt-in via HP_TUNE=1 so the normal retrain stays fast.

Used by total_bases_v1 / hits_v1 train_model when HP_TUNE is set, which
retrain_and_promote --tune turns on for the candidate.
"""
import numpy as np

from props.utils.logging import log

N_TRIALS = 40


def tune_lgb(fit_df, val_df, feature_keys, objective="poisson",
             weight=None, n_trials=N_TRIALS, seed=42) -> dict:
    """Search LGB params on (fit→val); return the best param dict (val MAE)."""
    import lightgbm as lgb
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    Xf, yf = fit_df[feature_keys], fit_df["y"]
    Xv, yv = val_df[feature_keys], val_df["y"]
    yv_arr = np.asarray(yv, dtype=float)

    def _objective(trial):
        params = {
            "objective": objective, "metric": "mae", "verbose": -1, "seed": seed,
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.10, log=True),
            "num_leaves": trial.suggest_int("num_leaves", 15, 63),
            "min_data_in_leaf": trial.suggest_int("min_data_in_leaf", 50, 300),
            "feature_fraction": trial.suggest_float("feature_fraction", 0.6, 1.0),
            "bagging_fraction": trial.suggest_float("bagging_fraction", 0.6, 1.0),
            "bagging_freq": 5,
            "lambda_l1": trial.suggest_float("lambda_l1", 1e-3, 5.0, log=True),
            "lambda_l2": trial.suggest_float("lambda_l2", 1e-3, 5.0, log=True),
        }
        dtrain = lgb.Dataset(Xf, yf, weight=weight)
        dval = lgb.Dataset(Xv, yv, reference=dtrain)
        m = lgb.train(params, dtrain, num_boost_round=2000, valid_sets=[dval],
                      callbacks=[lgb.early_stopping(50, verbose=False)])
        pred = m.predict(Xv, num_iteration=m.best_iteration)
        return float(np.mean(np.abs(pred - yv_arr)))

    study = optuna.create_study(direction="minimize",
                                sampler=optuna.samplers.TPESampler(seed=seed))
    study.optimize(_objective, n_trials=n_trials, show_progress_bar=False)
    log.info("hp_tune_complete", best_mae=round(study.best_value, 4),
             trials=n_trials, best_params=study.best_params)
    return study.best_params
