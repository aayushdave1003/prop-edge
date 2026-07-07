# Data provenance & risk

prop-edge is a **research / paper-tracking** project. It does not place bets, take
payment, or redistribute third-party data. This file documents where every input
comes from, how it's accessed, and the risk each carries — so the risky parts are
visible and isolated, not buried.

## Summary of risk

| Source | What | Access | Risk |
|---|---|---|---|
| **PrizePicks** | prop lines (projections) | **unofficial scrape**, residential proxy | **HIGH — the main risk** |
| The Odds API | market/sharp odds → `market_edge` | paid API key (licensed) | low (ToS-clean, metered) |
| ESPN | box scores, schedules, headshots, live | public endpoints, unofficial | medium (fragile, unofficial) |
| MLB Stats API | box scores, schedules, pitchers | public `statsapi.mlb.com` | low–medium |
| stats.nba.com (`nba_api`) | (fallback only) | blocked on datacenter IPs | n/a — replaced by ESPN |
| Injuries / weather | player status, park weather | public sources | low–medium |

## The PrizePicks scrape — the one to worry about

- **What:** `GET https://api.prizepicks.com/projections` (public JSON), parsed into
  `prop_lines`. This is the line the model is compared against — the core input.
- **How:** `props/ingest/prizepicks.py`. PrizePicks blocks datacenter IPs, so on
  GitHub Actions the request is routed through a **rotating residential proxy**
  (`PRIZEPICKS_PROXY`). That is the operational tell that this access is unofficial.
- **Risk, stated plainly:**
  - **ToS / legal:** scraping a public endpoint you don't have a data agreement
    with is against PrizePicks' terms. This project mitigates by being
    non-commercial, low-volume (a few pulls/day), and never redistributing PP data
    — but the risk is real and would need a licensed feed before any commercial use.
  - **Fragility:** an unofficial endpoint can change shape, rate-limit, or block at
    any time and silently zero out the slate. The proxy can also fail.
  - **Single point of failure:** no live lines → no picks that day.

### How it's isolated (the seam)

The risky fetch is quarantined behind one interface, `props/ingest/line_feed.py`:

- `LineFeed` — the interface (`fetch_raw() -> {"data", "included"}`).
- `PrizePicksFeed` — the current, unofficial scrape (default).
- `LicensedFeed` — a stub for an official / licensed feed; wire a ToS-clean client
  here and set `LINE_FEED=licensed` to swap the entire pipeline over with **no**
  downstream changes. Parsing/landing is unaffected — only the fetch swaps.

```
LINE_FEED=prizepicks   # default — unofficial scrape
LINE_FEED=licensed     # official/paid feed (implement LicensedFeed.fetch_raw)
```

This is the migration path off the risk: implement one class, flip one env var.

## Other sources

- **The Odds API** (`ODDS_API_KEY`) — a paid, licensed feed for market odds; drives
  `market_edge` and CLV. ToS-clean and metered (see the quota note in ops docs).
  Degrades gracefully when the quota is exhausted (`market_edge` goes flat).
- **ESPN** — box scores, schedules, game context, live in-game stats, and
  headshots/logos come from ESPN's public (unofficial) endpoints. Used because
  stats.nba.com blocks datacenter IPs. Fragile but low-stakes: a miss delays
  settlement, it doesn't fabricate results. No redistribution.
- **MLB Stats API** (`statsapi.mlb.com`) — public MLB endpoints for box scores,
  schedules, and probable pitchers. Used to confirm `Final` status and settle.
- **Injuries / weather** — player injury status and ballpark weather from public
  sources, used as model features and display badges; best-effort, fail-safe.

## Posture

Research and paper-tracking only. No wagering, no payments, no resale of any
sourced data. Before any commercial or public-product use, the PrizePicks scrape
must be replaced with a licensed feed via the `LicensedFeed` seam above, and the
paid feeds' terms re-reviewed for redistribution.
