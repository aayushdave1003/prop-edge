"""CBB bettable-player assessment — the November revisit the June build deferred.

THE QUESTION
------------
June's cbb_models_v1 run found CBB props model poorly on ALL players (best combo
+1.4%; points/assists/threes lose to the season-avg baseline). The open hypothesis
was that BETTABLE players — the high-minute, established names a book actually
posts — might be more predictable than the full 10k-player pool. This harness
tests exactly that.

THE ANSWER (2026-11-09): REFUTED — the edge SHRINKS as players get more
established, the opposite of the hypothesis. Eval on last-10-avg-minutes >= 25
drops PRA from +1.05% (all players) to +0.31%; >= 30 to +0.01%; training on the
high-minute subset doesn't help; and the 30 Sleeper-posted players with history
(n=233 test rows) show the same shape. A star's season average IS the prediction
— the intrinsic-variance verdict from June, now confirmed on the slice that
matters. Sleeper DOES post CBB lines (~86/day at 2026-27 tip-off, sport code
`cbb`, exactly our 5 trained markets), so the lane was bettable — the models just
add nothing on bettable players. CBB is dropped; see ROADMAP → New sports.

Replicates cbb_models_v1's exact trainer (params, features, time split) but
sweeps train/eval configurations:
  all/all        - June's original assessment, as the control
  all/min25      - eval only rows where last_10_avg_minutes >= 25
  all/min30      - eval only rows where last_10_avg_minutes >= 30
  min25/min25    - train AND eval on the high-minute subset
  all/sleeper    - eval only players Sleeper posted CBB lines for (snapshot below)

Read-only: SELECTs only, saves no artifacts, writes nothing. Re-runnable any
season (refresh SLEEPER_PLAYERS from /lines/available first):

    python -m props.models.cbb_bettable_eval
"""
import re
import unicodedata

import numpy as np
import pandas as pd
import lightgbm as lgb

from props.utils.db import engine
from props.utils.logging import configure_logging, log

# Sleeper-posted CBB players, snapshotted 2026-11-09 (86 lines, 36 players).
SLEEPER_PLAYERS = [
    "Acaden Lewis", "Ace Buckner", "Andrej Stojakovic", "Angelo Ciaravino",
    "Bol Dengdit", "Brady Koehler", "Braeden Shrewsberry", "Braylon Mullins",
    "C.J. Cox", "Caden Pierce", "Chance Trujillo", "Cole Certa",
    "Daniel Jacobsen", "Dylan Faulkner", "Elijah Fisher", "Gavin Doty",
    "Gicarri Harris", "Isaac Asuma", "Jaden Matingou", "Jaeden Mustaf",
    "Jake West", "Jayden Ross", "Jaylen Crocker-Johnson", "John Mobley Jr.",
    "LeJuan Watts", "Malik Reneau", "Mark Morano", "Markus Burton",
    "Na'jai Hines", "Nik Khamenia", "Sadiq White Jr.", "Simeon Wilcher",
    "Stefan Vaaks", "Tanner Cuff", "Terry Copeland Jr.", "Tomislav Ivisic",
]

# The 5 markets Sleeper posts for CBB — all ones cbb_models_v1 trains.
TARGETS = {
    "points": ["points"],
    "rebounds": ["rebounds"],
    "assists": ["assists"],
    "threes_made": ["threes_made"],
    "pts_rebs_asts": ["points", "rebounds", "assists"],
}

FEATURE_KEYS = [
    "last_5_avg_points", "last_10_avg_points", "last_20_avg_points", "season_avg_points",
    "last_5_avg_rebounds", "last_10_avg_rebounds", "last_20_avg_rebounds", "season_avg_rebounds",
    "last_5_avg_assists", "last_10_avg_assists", "last_20_avg_assists", "season_avg_assists",
    "last_5_avg_threes_made", "last_10_avg_threes_made", "season_avg_threes_made",
    "last_5_avg_minutes", "last_10_avg_minutes", "last_20_avg_minutes", "season_avg_minutes",
    "last_10_avg_fg_attempted", "last_10_avg_turnovers",
    "last_10_avg_steals", "last_10_avg_blocks",
    "days_rest", "games_played_season",
]

PARAMS = {"objective": "poisson", "metric": ["poisson", "mae"], "learning_rate": 0.05,
          "num_leaves": 31, "min_data_in_leaf": 50, "feature_fraction": 0.8,
          "bagging_fraction": 0.8, "bagging_freq": 5, "lambda_l2": 1.0,
          "verbose": -1, "seed": 42}


def norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", s.lower())
    return re.sub(r"[^a-z]", "", s)


def load():
    log.info("loading_cbb_eval_data")
    df = pd.read_sql("""
        SELECT pg.player_game_id, g.game_date, p.full_name, pg.derived, pg.stats
        FROM player_games pg
        JOIN games g USING (game_id)
        JOIN players p USING (player_id)
        WHERE g.sport_code='cbb' AND pg.minutes_played >= 5
    """, engine)
    log.info("loaded_cbb_rows", n=len(df),
             dates=f"{df['game_date'].min()}..{df['game_date'].max()}")
    df["game_date"] = pd.to_datetime(df["game_date"])
    derived = pd.json_normalize(df["derived"])
    stats = pd.json_normalize(df["stats"])
    out = pd.DataFrame({"game_date": df["game_date"].values,
                        "name_norm": df["full_name"].map(norm).values})
    for k in FEATURE_KEYS:
        out[k] = pd.to_numeric(derived.get(k, 0), errors="coerce").fillna(0)
    for s in ("points", "rebounds", "assists", "threes_made"):
        out[f"_st_{s}"] = pd.to_numeric(stats.get(s, 0), errors="coerce").fillna(0)
    return out[out["last_10_avg_minutes"] > 0].copy()


def run_config(out, label, train_mask, eval_mask):
    print(f"\n=== {label} ===", flush=True)
    for tname, comps in TARGETS.items():
        df = out.copy()
        df["y"] = sum(df[f"_st_{c}"] for c in comps).astype(int)
        df["baseline"] = sum(df[f"season_avg_{c}"] for c in comps)
        cutoff = df["game_date"].quantile(0.8)
        tr = df[(df.game_date < cutoff) & train_mask]
        te = df[(df.game_date >= cutoff) & eval_mask]
        if len(tr) < 2000 or len(te) < 80:
            print(f"  {tname:16} SKIPPED (train n={len(tr)}, eval n={len(te)})")
            continue
        vc = tr["game_date"].quantile(0.85)
        fit, val = tr[tr.game_date < vc], tr[tr.game_date >= vc]
        model = lgb.train(PARAMS, lgb.Dataset(fit[FEATURE_KEYS], fit["y"]),
                          num_boost_round=800,
                          valid_sets=[lgb.Dataset(val[FEATURE_KEYS], val["y"])],
                          callbacks=[lgb.early_stopping(40, verbose=False)])
        pred = model.predict(te[FEATURE_KEYS], num_iteration=model.best_iteration)
        yv = te["y"].values
        mae_m = float(np.mean(np.abs(pred - yv)))
        mae_b = float(np.mean(np.abs(te["baseline"].values - yv)))
        imp = 100 * (mae_b - mae_m) / mae_b
        print(f"  {tname:16} model MAE {mae_m:7.4f}  baseline {mae_b:7.4f}  "
              f"n={len(te):6d}  improvement {imp:+6.2f}%", flush=True)


def main():
    configure_logging()
    out = load()
    sleeper_set = {norm(n) for n in SLEEPER_PLAYERS}
    slp = out["name_norm"].isin(sleeper_set)
    m25 = out["last_10_avg_minutes"] >= 25
    m30 = out["last_10_avg_minutes"] >= 30
    all_mask = pd.Series(True, index=out.index)
    print(f"rows: all={len(out)}  min25={int(m25.sum())}  min30={int(m30.sum())}  "
          f"sleeper={int(slp.sum())} "
          f"({out.loc[slp, 'name_norm'].nunique()}/{len(sleeper_set)} players matched)")

    run_config(out, "all/all (control — replicates cbb_models_v1)", all_mask, all_mask)
    run_config(out, "all/min25", all_mask, m25)
    run_config(out, "all/min30", all_mask, m30)
    run_config(out, "min25/min25", m25, m25)
    run_config(out, "all/sleeper-posted", all_mask, slp)


if __name__ == "__main__":
    main()
