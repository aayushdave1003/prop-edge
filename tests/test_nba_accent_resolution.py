"""NBA player resolution must be accent-insensitive.

`resolve_player` matched on pg_trgm `similarity(full_name, :name) > 0.8`, which
compares raw bytes. An accent costs several trigrams, so every accented player
silently forked into a second `espn_` row when ESPN (unaccented) and
stats.nba.com (accented) disagreed:

    similarity('Pacôme Dadiet', 'Pacome Dadiet')    = 0.647
    similarity('Egor Dëmin',    'Egor Demin')       = 0.571
    similarity('Dennis Schröder','Dennis Schroder') = 0.684

18 players were split by the 2026 preseason, including Jokić, Dončić, Porziņģis
and Vučević -- their recent games stranded away from the multi-season history
the rolling features are built from.
"""
from props.ingest.nba_boxscores import _accent_fold
from props.ingest.sleeper import _norm as sleeper_norm

# (accented spelling, ESPN's unaccented spelling) — the real pairs found in prod.
REAL_PAIRS = [
    ("Nikola Jokić", "Nikola Jokic"),
    ("Luka Dončić", "Luka Doncic"),
    ("Kristaps Porziņģis", "Kristaps Porzingis"),
    ("Nikola Vučević", "Nikola Vucevic"),
    ("Pacôme Dadiet", "Pacome Dadiet"),
    ("Egor Dëmin", "Egor Demin"),
    ("Dennis Schröder", "Dennis Schroder"),
    ("Vít Krejčí", "Vit Krejci"),
    ("Chris Mañon", "Chris Manon"),
    ("Yanic Konan Niederhäuser", "Yanic Konan Niederhauser"),
]


def test_accented_and_plain_spellings_fold_together():
    for accented, plain in REAL_PAIRS:
        assert _accent_fold(accented) == _accent_fold(plain), f"{accented} != {plain}"


def test_fold_agrees_with_the_line_feed_normaliser():
    """The fold only prevents forks if it makes the SAME judgement as the
    normaliser that matches a line feed's name to a player."""
    for accented, plain in REAL_PAIRS:
        assert _accent_fold(accented) == sleeper_norm(accented)
        assert _accent_fold(plain) == sleeper_norm(plain)


def test_fold_does_not_collapse_different_players():
    """Folding must not merge people who merely look similar."""
    assert _accent_fold("Jaylen Brown") != _accent_fold("Jaylin Williams")
    assert _accent_fold("Nikola Jokić") != _accent_fold("Nikola Jović")
    assert _accent_fold("Bogdan Bogdanović") != _accent_fold("Bojan Bogdanović")


def test_fold_handles_empty_and_punctuation():
    assert _accent_fold("") == ""
    assert _accent_fold(None) == ""
    assert _accent_fold("J.T. Miller") == "jt miller"
