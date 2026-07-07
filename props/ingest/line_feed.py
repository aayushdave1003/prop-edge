"""Line-feed seam — isolates WHERE prop lines come from behind one interface.

The default source is a scrape of PrizePicks' public projections endpoint. That
scrape is the single biggest legal/operational risk in prop-edge (unofficial
access, and a residential proxy to dodge datacenter blocks — see PROVENANCE.md).
This module quarantines that risk behind a ``LineFeed`` seam so the fetch can be
swapped for a licensed/official feed by changing one env var, with no pipeline
rewrite:

    LINE_FEED=prizepicks   (default)  ->  PrizePicksFeed  (unofficial scrape)
    LINE_FEED=licensed                ->  LicensedFeed    (stub; wire a real feed)

Only the FETCH — the risky network/ToS boundary — sits behind the seam. Parsing
and landing into ``prop_lines`` is prop-edge's own logic and stays put.
"""
from __future__ import annotations

import os
from typing import Protocol

from props.utils.logging import log


class LineFeed(Protocol):
    """A source of raw prop-line projections. ``fetch_raw`` returns the payload
    shape the parser expects: ``{"data": [...], "included": [...]}``."""

    source_name: str

    def fetch_raw(self) -> dict: ...


class PrizePicksFeed:
    """The current source: PrizePicks' public projections endpoint, fetched
    through a residential proxy when configured. UNOFFICIAL — see PROVENANCE.md
    for the ToS / legal / fragility risk. Isolated here so it can be replaced
    without touching the ingest pipeline."""

    source_name = "prizepicks_scrape"

    def fetch_raw(self) -> dict:
        # Imported lazily to avoid a circular import (prizepicks imports this).
        from props.ingest.prizepicks import fetch_projections
        return fetch_projections()


class LicensedFeed:
    """Placeholder for an official / licensed line feed (a data partnership or a
    paid, ToS-clean API). Wire the real client here and set ``LINE_FEED=licensed``
    to swap the whole pipeline over with zero downstream changes — it must return
    the same shape as ``PrizePicksFeed.fetch_raw()``."""

    source_name = "licensed_feed"

    def fetch_raw(self) -> dict:
        raise NotImplementedError(
            "No licensed line feed is wired yet. Implement a ToS-clean client "
            "here and set LINE_FEED=licensed. See PROVENANCE.md."
        )


def get_line_feed() -> LineFeed:
    """Return the configured line feed (env ``LINE_FEED``, default prizepicks)."""
    name = os.getenv("LINE_FEED", "prizepicks").strip().lower()
    if name == "licensed":
        return LicensedFeed()
    if name != "prizepicks":
        log.warning("unknown_line_feed", requested=name, using="prizepicks")
    return PrizePicksFeed()
