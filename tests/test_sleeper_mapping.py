"""Guard the Sleeper wager_type -> stat_type mapping against silent dead ends.

Two production bugs motivated this file, both silent (no error, no alert — the
wager just counted as `unmodeled_stat` or the line never matched a model):

  1. `earned_runs` mapped to the stat_type "earned_runs", but the model is
     registered as "earned_runs_allowed" — so 2,126 Sleeper lines landed in
     prop_lines that `earned_runs_allowed_v1` could never pick from.
  2. The map was keyed on wager_type ALONE, so NFL "assists" (a defensive
     assisted tackle) resolved to the basketball assists stat.
"""
from props.ingest.sleeper import SPORTS, WAGER_TO_STAT
from props.models.registry import MODELS


def test_every_mapping_targets_a_real_model_for_that_sport():
    """A target that no model answers for that sport is a line that can never
    become a pick — the exact shape of the earned_runs_allowed bug."""
    registered = {(m.sport_code, m.stat_type) for m in MODELS}
    orphans = [(sp, wt, st) for (sp, wt), st in WAGER_TO_STAT.items()
               if (sp, st) not in registered]
    assert orphans == [], f"mappings with no model for that sport: {orphans}"


def test_mapping_is_keyed_on_sport_and_wager_type():
    """Keys must be (sport, wager_type) pairs, not bare wager_type strings."""
    for key in WAGER_TO_STAT:
        assert isinstance(key, tuple) and len(key) == 2, f"not a pair: {key!r}"
        sport, wager = key
        assert sport in SPORTS, f"{sport!r} maps wagers but is not ingested"
        assert isinstance(wager, str) and wager


def test_same_wager_type_may_differ_across_sports():
    """The whole point of pair-keying: one string, two sports, two meanings."""
    nhl_assists = WAGER_TO_STAT.get(("nhl", "assists"))
    assert nhl_assists == "assists"
    # NFL "assists" is an assisted tackle, which we do not model — it must NOT
    # silently inherit the basketball mapping.
    assert ("nfl", "assists") not in WAGER_TO_STAT


def test_sports_covers_every_registered_model_sport():
    """A sport with models but absent from SPORTS is dark — the NHL bug."""
    modelled = {m.sport_code for m in MODELS}
    missing = modelled - set(SPORTS)
    assert missing == set(), f"models exist but lines are never ingested: {missing}"
