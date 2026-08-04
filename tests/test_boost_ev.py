"""Unit tests for the promo/odds-boost tracker (pricing math) — no DB, no network."""
from datetime import date

from props.picks.boost_ev import american_to_decimal, _price_boost, _nudge_payload


def _sharp(over_prob=0.55, line=1.5, stat="total_bases"):
    return {("aaron judge", stat): [(line, over_prob)]}


def test_american_to_decimal():
    assert american_to_decimal(200) == 3.0          # +200 -> 3.0x
    assert abs(american_to_decimal(-110) - 1.90909) < 1e-4
    assert american_to_decimal(-200) == 1.5


def test_flags_overpaying_boost():
    # sharp says 55% over; boost pays +200 (3.0x) -> EV = .55*3.0-1 = +0.65 (take it)
    b = _price_boost("Aaron Judge", "total_bases", 1.5, "over", 200, _sharp(0.55))
    assert b["true_prob"] == 0.55
    assert 0.6 < b["ev"] < 0.7


def test_marks_bad_price_negative():
    # same 55% true prob, but a stingy -300 (1.333x) -> EV = .55*1.333-1 < 0 (pass)
    b = _price_boost("Aaron Judge", "total_bases", 1.5, "over", -300, _sharp(0.55))
    assert b["ev"] is not None and b["ev"] < 0


def test_prices_under_side():
    # sharp 55% over => 45% under; a +200 boost on the under -> .45*3.0-1 = +0.35
    b = _price_boost("Aaron Judge", "total_bases", 1.5, "under", 200, _sharp(0.55))
    assert abs(b["true_prob"] - 0.45) < 1e-9
    assert 0.3 < b["ev"] < 0.4


def test_reprices_when_boost_line_differs_from_sharp():
    # sharp anchor is TB 1.5 @ 55% over; boost is on TB 2.5 -> Poisson re-price lowers
    # the true over prob, so the same +200 is worth less than at 1.5.
    at15 = _price_boost("Aaron Judge", "total_bases", 1.5, "over", 200, _sharp(0.55))
    at25 = _price_boost("Aaron Judge", "total_bases", 2.5, "over", 200, _sharp(0.55))
    assert at25["true_prob"] < at15["true_prob"]


def test_unpricable_no_sharp_reference():
    b = _price_boost("Nobody Here", "total_bases", 1.5, "over", 200, _sharp(0.55))
    assert b["ev"] is None and "no sharp line" in b["note"]


def test_unpricable_unreliable_anchor():
    # sharp anchor 95% is outside [0.20,0.80] (alt/thin line) -> not priced
    b = _price_boost("Aaron Judge", "total_bases", 1.5, "over", 200, _sharp(0.95))
    assert b["ev"] is None and "anchor" in b["note"]


def test_nudge_payload_carries_the_command():
    # empty-day reminder must tell the user how to enter boosts, no track record yet
    p = _nudge_payload(date(2026, 8, 4), {"n": 0, "roi": 0, "lo": 0, "hi": 0, "hit": 0, "verdict": "—"})
    body = p["embeds"][0]["description"]
    assert "--add-many" in body
    assert "none entered" in p["embeds"][0]["title"]
    assert "Track record" not in body  # nothing settled yet -> no phantom verdict


def test_nudge_payload_appends_track_record_once_filled():
    p = _nudge_payload(date(2026, 8, 4), {"n": 40, "roi": 0.12, "lo": 0.03, "hi": 0.21, "hit": 55, "verdict": "profitable"})
    assert "Track record" in p["embeds"][0]["description"]
