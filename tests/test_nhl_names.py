"""NHL full-name repair: guard the two assumptions it rests on.

NHL box scores stored abbreviated names ("B. Montour") while every line feed
uses full ones ("Brandon Montour"), so box scores and lines never matched and
the sport produced no picks. The repair renames those rows from the NHL roster
endpoint; `nhl_boxscores` then refuses to downgrade a full name back to an
abbreviation.
"""
import importlib.util
from pathlib import Path

from props.ingest.nhl_boxscores import ABBREV_NAME
from props.ingest.sleeper import _norm as sleeper_norm

_spec = importlib.util.spec_from_file_location(
    "repair_nhl_names", Path(__file__).resolve().parent.parent / "scripts" / "repair_nhl_names.py")
repair = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(repair)


def test_abbrev_detects_initial_form_only():
    for abbreviated in ("B. Montour", "V. Dunn", "J. Staal", "S. Aho"):
        assert ABBREV_NAME.match(abbreviated), f"{abbreviated!r} should read as abbreviated"
    for full in ("Brandon Montour", "Vince Dunn", "Jordan Staal", "Joel Eriksson Ek"):
        assert not ABBREV_NAME.match(full), f"{full!r} should read as full"


def test_repair_norm_matches_the_feed_norm():
    """The repair only helps if its normaliser agrees with the one that does the
    actual line matching -- otherwise it writes names the feed still cannot find."""
    for name in ("Brandon Montour", "Joel Eriksson Ek", "Tim Stützle",
                 "J.T. Miller", "Alex Newhook", "Zachary Bolduc"):
        assert repair._norm(name) == sleeper_norm(name), name


def test_repair_norm_bridges_nothing_by_accident():
    """An abbreviated name must NOT normalise onto its full form -- that is
    precisely why the stored value had to be repaired rather than matched around."""
    assert repair._norm("B. Montour") != repair._norm("Brandon Montour")
