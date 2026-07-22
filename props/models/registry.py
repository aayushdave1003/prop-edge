"""Registry of trained models and how to use them.

Each entry describes a (sport, stat_type) prediction:
  - model_path, meta_path: where to load from
  - role: 'pitcher' or 'batter' (drives which inference path to use)
  - stat_type: matches prop_lines.stat_type for line matching
  - prediction_distribution: 'poisson' for now
"""
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ModelEntry:
    name: str
    sport_code: str
    stat_type: str
    role: str
    model_path: Path
    meta_path: Path
    prediction_distribution: str = "poisson"


MODELS = [
    ModelEntry(
        name="strikeouts_v1",
        sport_code="mlb",
        stat_type="strikeouts_pitcher",
        role="pitcher",
        model_path=Path("models/strikeouts_v1.txt"),
        meta_path=Path("models/strikeouts_v1_meta.json"),
    ),
    ModelEntry(
        name="hits_v1",
        sport_code="mlb",
        stat_type="hits",
        role="batter",
        model_path=Path("models/hits_v1.txt"),
        meta_path=Path("models/hits_v1_meta.json"),
    ),
    ModelEntry(
        name="rbis_v1",
        sport_code="mlb",
        stat_type="rbis",
        role="batter",
        model_path=Path("models/rbis_v1.txt"),
        meta_path=Path("models/rbis_v1_meta.json"),
    ),
    ModelEntry(
        name="total_bases_v1",
        sport_code="mlb",
        stat_type="total_bases",
        role="batter",
        model_path=Path("models/total_bases_v1.txt"),
        meta_path=Path("models/total_bases_v1_meta.json"),
    ),
    ModelEntry(
        name="strikeouts_batter_v1",
        sport_code="mlb",
        stat_type="strikeouts_batter",
        role="batter",
        model_path=Path("models/strikeouts_batter_v1.txt"),
        meta_path=Path("models/strikeouts_batter_v1_meta.json"),
    ),
    ModelEntry(
        name="hits_runs_rbis_v1",
        sport_code="mlb",
        stat_type="hits_runs_rbis",
        role="batter",
        model_path=Path("models/hits_runs_rbis_v1.txt"),
        meta_path=Path("models/hits_runs_rbis_v1_meta.json"),
    ),
    ModelEntry(
        name="earned_runs_allowed_v1",
        sport_code="mlb",
        stat_type="earned_runs_allowed",
        role="pitcher",
        model_path=Path("models/earned_runs_allowed_v1.txt"),
        meta_path=Path("models/earned_runs_allowed_v1_meta.json"),
    ),
    ModelEntry(
        name="hits_allowed_v1",
        sport_code="mlb",
        stat_type="hits_allowed",
        role="pitcher",
        model_path=Path("models/hits_allowed_v1.txt"),
        meta_path=Path("models/hits_allowed_v1_meta.json"),
    ),
    ModelEntry(
        name="nba_points_v1",
        sport_code="nba",
        stat_type="points",
        role="player",
        model_path=Path("models/nba_points_v1.txt"),
        meta_path=Path("models/nba_points_v1_meta.json"),
    ),
    ModelEntry(
        name="nba_rebounds_v1",
        sport_code="nba",
        stat_type="rebounds",
        role="player",
        model_path=Path("models/nba_rebounds_v1.txt"),
        meta_path=Path("models/nba_rebounds_v1_meta.json"),
    ),
    ModelEntry(
        name="nba_assists_v1",
        sport_code="nba",
        stat_type="assists",
        role="player",
        model_path=Path("models/nba_assists_v1.txt"),
        meta_path=Path("models/nba_assists_v1_meta.json"),
    ),
    ModelEntry(
        name="nba_threes_made_v1",
        sport_code="nba",
        stat_type="threes_made",
        role="player",
        model_path=Path("models/nba_threes_made_v1.txt"),
        meta_path=Path("models/nba_threes_made_v1_meta.json"),
    ),
    ModelEntry(
        name="mlb_home_runs_v1",
        sport_code="mlb",
        stat_type="home_runs",
        role="batter",
        model_path=Path("models/mlb_home_runs_v1.txt"),
        meta_path=Path("models/mlb_home_runs_v1_meta.json"),
        prediction_distribution="binary",
    ),
    ModelEntry(
        name="wnba_points_v1",
        sport_code="wnba",
        stat_type="points",
        role="player",
        model_path=Path("models/wnba_points_v1.txt"),
        meta_path=Path("models/wnba_points_v1_meta.json"),
    ),
    ModelEntry(
        name="wnba_rebounds_v1",
        sport_code="wnba",
        stat_type="rebounds",
        role="player",
        model_path=Path("models/wnba_rebounds_v1.txt"),
        meta_path=Path("models/wnba_rebounds_v1_meta.json"),
    ),
    ModelEntry(
        name="wnba_assists_v1",
        sport_code="wnba",
        stat_type="assists",
        role="player",
        model_path=Path("models/wnba_assists_v1.txt"),
        meta_path=Path("models/wnba_assists_v1_meta.json"),
    ),
    ModelEntry(
        name="nhl_goals_v1",
        sport_code="nhl",
        stat_type="goals",
        role="player",
        model_path=Path("models/nhl_goals_v1.txt"),
        meta_path=Path("models/nhl_goals_v1_meta.json"),
    ),
    ModelEntry(
        name="nhl_assists_v1",
        sport_code="nhl",
        stat_type="assists",
        role="player",
        model_path=Path("models/nhl_assists_v1.txt"),
        meta_path=Path("models/nhl_assists_v1_meta.json"),
    ),
    ModelEntry(
        name="nhl_saves_v1",
        sport_code="nhl",
        stat_type="saves",
        role="player",
        model_path=Path("models/nhl_saves_v1.txt"),
        meta_path=Path("models/nhl_saves_v1_meta.json"),
    ),
    # NFL — yards markets (rushing/receiving beat baseline; nfl_models_v1 ships only
    # winners, so a missing .txt just skips at predict time). Trained on L1 (mean
    # yardage); score_and_edge converts the mean to P(over) via the Poisson CDF.
    ModelEntry(
        name="nfl_rushing_yards_v1",
        sport_code="nfl",
        stat_type="rushing_yards",
        role="player",
        model_path=Path("models/nfl_rushing_yards_v1.txt"),
        meta_path=Path("models/nfl_rushing_yards_v1_meta.json"),
    ),
    ModelEntry(
        name="nfl_receiving_yards_v1",
        sport_code="nfl",
        stat_type="receiving_yards",
        role="player",
        model_path=Path("models/nfl_receiving_yards_v1.txt"),
        meta_path=Path("models/nfl_receiving_yards_v1_meta.json"),
    ),
    # receptions is a clean Poisson fit (unlike the L1-yards markets); +1.30% OOS.
    ModelEntry(
        name="nfl_receptions_v1",
        sport_code="nfl",
        stat_type="receptions",
        role="player",
        model_path=Path("models/nfl_receptions_v1.txt"),
        meta_path=Path("models/nfl_receptions_v1_meta.json"),
    ),
]

# NBA/WNBA combo markets — direct summed-target Poisson models
# (props.models.basketball_combos_v1). stat_type matches prop_lines.
MODELS += [
    ModelEntry(
        name=f"{sp}_{combo}_v1", sport_code=sp, stat_type=combo, role="player",
        model_path=Path(f"models/{sp}_{combo}_v1.txt"),
        meta_path=Path(f"models/{sp}_{combo}_v1_meta.json"),
    )
    for sp in ("nba", "wnba")
    for combo in ("pts_rebs_asts", "pts_rebs", "pts_asts", "rebs_asts")
]


def get_models_for_sport(sport_code: str) -> list[ModelEntry]:
    return [m for m in MODELS if m.sport_code == sport_code]
