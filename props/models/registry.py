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
]


def get_models_for_sport(sport_code: str) -> list[ModelEntry]:
    return [m for m in MODELS if m.sport_code == sport_code]
