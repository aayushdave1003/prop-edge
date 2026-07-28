"""Pluggable SHARP-ODDS reference for the market-arbitrage finder (sleeper_arb).

The arb bets Sleeper lines that are soft vs a SHARP no-vig consensus — so the
finder is only as good as that reference. The default (free Odds API DK/FD, one
AM snapshot, Poisson-re-priced) is laggy and not truly sharp, which is likely why
the live arb LOST (−17.8% at n=81). This seam swaps in a genuinely-sharp,
real-time source (Pinnacle / Circa, or an aggregator like OddsJam / Unabated)
WITHOUT touching the arb logic, and lets you A/B it against the free path.

Every feed returns the SAME shape `build_market_probs` does —
    {(player_name_lower, stat_type, line_value): no_vig_over_prob}
— so sleeper_arb consumes any feed identically. Select via the SHARP_FEED env var:

  SHARP_FEED=odds_api   (default) — free Odds API DK/FD consensus
  SHARP_FEED=oddsjam    — OddsJam API   (needs ODDSJAM_API_KEY)   [stub — wire build_probs]
  SHARP_FEED=unabated   — Unabated API  (needs UNABATED_API_KEY)  [stub — wire build_probs]
  SHARP_FEED=pinnacle   — Pinnacle no-vig (needs PINNACLE_API_KEY) [stub — wire build_probs]

To wire a paid provider: implement its `build_probs()` to fetch the provider's
real-time sharp lines and return the no-vig over-prob per (player, stat, line).
Then run the finder with SHARP_FEED=<name>; picks are tagged by feed so
`sleeper_arb --roi` reports realized ROI PER FEED — the A/B.
"""
import os
from datetime import date

from props.utils.logging import log


class SharpFeed:
    name = "base"

    def build_probs(self, run_date: date, sports=("mlb",)) -> dict:
        """{(player_name_lower, stat_type, line_value): no_vig_over_prob}."""
        raise NotImplementedError


class OddsApiSharpFeed(SharpFeed):
    """Free default — DK/FD no-vig consensus via the Odds API. NOT truly sharp
    (the reference the live arb lost with); the baseline to beat."""
    name = "odds_api"

    def build_probs(self, run_date, sports=("mlb",)):
        from props.ingest.market_odds import build_market_probs
        return build_market_probs(run_date, sports=sports)


class _PaidStub(SharpFeed):
    """Paid provider slot. Returns {} (finder no-ops) if the key is unset; raises a
    clear TODO if the key IS set but build_probs isn't wired — so a mis-config is
    loud, not a silent 'no soft lines'."""
    env_key = ""

    def build_probs(self, run_date, sports=("mlb",)):
        if not os.getenv(self.env_key):
            log.warning("sharp_feed_no_key", feed=self.name, need=self.env_key)
            return {}
        raise NotImplementedError(
            f"{self.name}: {self.env_key} is set but build_probs() isn't wired. Implement the "
            f"provider's real-time fetch/parse to return {{(player_name_lower, stat_type, "
            f"line_value): no_vig_over_prob}}, then A/B via SHARP_FEED={self.name}.")


class OddsJamSharpFeed(_PaidStub):
    name = "oddsjam"; env_key = "ODDSJAM_API_KEY"


class UnabatedSharpFeed(_PaidStub):
    name = "unabated"; env_key = "UNABATED_API_KEY"


class PinnacleSharpFeed(_PaidStub):
    name = "pinnacle"; env_key = "PINNACLE_API_KEY"


_FEEDS = {f.name: f for f in (OddsApiSharpFeed, OddsJamSharpFeed, UnabatedSharpFeed, PinnacleSharpFeed)}


def get_sharp_feed() -> SharpFeed:
    """The active sharp reference, per the SHARP_FEED env var (default odds_api)."""
    name = os.getenv("SHARP_FEED", "odds_api").lower()
    feed = _FEEDS.get(name)
    if feed is None:
        log.warning("sharp_feed_unknown", requested=name, using="odds_api")
        feed = OddsApiSharpFeed
    return feed()
