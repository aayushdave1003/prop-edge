"""Line-feed seam — isolates WHERE prop lines come from behind one interface, and
dispatches the ingest to the configured book. One env var swaps the whole pipeline:

    LINE_FEED=prizepicks   ->  PrizePicksFeed  (unofficial scrape — Cloudflare-walled 2026-07)
    LINE_FEED=sleeper      ->  SleeperFeed     (open public API — the live source)
    LINE_FEED=licensed     ->  LicensedFeed    (stub; wire a paid feed)

Each feed owns its full ``run()`` (fetch + parse + land into prop_lines). Run the
active one with ``python -m props.ingest.line_feed`` — that's what the daily/refresh
pipeline calls, so pointing at a different book is one env change, no code edits.
``fetch_raw()`` (raw payload) is kept for the PP path + diagnostics.
"""
from __future__ import annotations

import os
from typing import Protocol

from props.utils.logging import log


class LineFeed(Protocol):
    """A source of prop lines. ``run()`` does the full ingest (fetch → land)."""

    source_name: str

    def fetch_raw(self) -> dict: ...
    def run(self) -> None: ...


class PrizePicksFeed:
    """PrizePicks' public projections endpoint via a residential proxy. UNOFFICIAL
    and Cloudflare-walled since 2026-07-07 (see PROVENANCE.md) — kept for when/if
    the block lifts, but not the live source."""

    source_name = "prizepicks_scrape"

    def fetch_raw(self) -> dict:
        from props.ingest.prizepicks import fetch_projections   # lazy: avoids a cycle
        return fetch_projections()

    def run(self) -> None:
        from props.ingest.prizepicks import run
        run()


class SleeperFeed:
    """Sleeper Picks' public ``/lines/available`` API — open (no auth, no Cloudflare),
    the live line source after PP + Underdog were both CF-walled. A different book
    (per-pick odds), so tracking on it is a new track record measured by ROI
    (props.models.odds_track), not PP's flat win-rate. See props/ingest/sleeper.py."""

    source_name = "sleeper"

    def fetch_raw(self) -> dict:
        from props.ingest.sleeper import _get
        return {"data": _get("/lines/available")}

    def run(self) -> None:
        from props.ingest.sleeper import run
        run()


class LicensedFeed:
    """Placeholder for an official / licensed feed (a data partnership or paid API)."""

    source_name = "licensed_feed"

    def fetch_raw(self) -> dict:
        raise NotImplementedError("No licensed feed wired. See PROVENANCE.md.")

    def run(self) -> None:
        raise NotImplementedError("No licensed feed wired. See PROVENANCE.md.")


_FEEDS = {"prizepicks": PrizePicksFeed, "sleeper": SleeperFeed, "licensed": LicensedFeed}


def get_line_feed() -> LineFeed:
    """Return the configured line feed (env ``LINE_FEED``, default prizepicks)."""
    name = os.getenv("LINE_FEED", "prizepicks").strip().lower()
    feed = _FEEDS.get(name)
    if feed is None:
        log.warning("unknown_line_feed", requested=name, using="prizepicks")
        feed = PrizePicksFeed
    return feed()


if __name__ == "__main__":
    # Ingest dispatcher: run the active book's full ingest. The pipeline calls this.
    get_line_feed().run()
