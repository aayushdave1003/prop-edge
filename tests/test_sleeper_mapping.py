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


# ── namesakes ───────────────────────────────────────────────────────────────
# Normalised names are not unique. MLB has 8 genuine namesake pairs (two Will
# Smiths -- a catcher with 444 games and 273 lines, and a pitcher with 110
# games) and NHL has 12. The player map was a plain dict, so the LAST row of an
# unordered query silently won and a line could attach to the wrong player.

from props.ingest.sleeper import pick_candidate

CATCHER = (595, 23)     # Will Smith, C
PITCHER = (1885, 9)     # Will Smith, P


def test_single_candidate_needs_no_team():
    assert pick_candidate([CATCHER], None) == CATCHER


def test_namesakes_are_split_by_the_lines_own_team():
    assert pick_candidate([CATCHER, PITCHER], 23) == CATCHER
    assert pick_candidate([CATCHER, PITCHER], 9) == PITCHER


def test_namesake_without_a_team_is_dropped_not_guessed():
    """A wrong attribution corrupts both the pricing features and the settled
    record; a dropped line is merely a missing row."""
    assert pick_candidate([CATCHER, PITCHER], None) is None


def test_namesake_with_an_unknown_team_is_dropped():
    assert pick_candidate([CATCHER, PITCHER], 999) is None


def test_namesake_on_the_same_team_is_dropped():
    """Two same-named players on one roster cannot be told apart by team."""
    assert pick_candidate([(1, 23), (2, 23)], 23) is None


# ── every registered sport must survive the pick-logging path ───────────────
# log_picks resolves a model's sport by prefix when the registry lookup misses,
# and renders the daily Discord post by iterating an explicit sport list. A
# sport missing from either is silent: picks get MLB's cutoff, or are written to
# the DB and never shown.

def test_every_registered_sport_resolves_and_renders():
    import inspect
    from props.picks import log_picks
    from props.models.registry import MODELS

    sports = {m.sport_code for m in MODELS}
    src = inspect.getsource(log_picks)
    for sport in sorted(sports):
        assert f'"{sport}"' in src, f"{sport} appears nowhere in log_picks"

    for m in MODELS:
        assert log_picks.sport_for_model(m.name, None) == m.sport_code, (
            f"{m.name} resolves to the wrong sport without a registry hint")
