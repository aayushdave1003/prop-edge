# prop-edge — Project Roadmap

Priorities: **P0** blocking/reliability · **P1** core value · **P2** quality/scale · **P3** future.
Status: ☐ todo · ◧ in progress.

**Open work only** — finished items auto-archive to [CHANGELOG.md](CHANGELOG.md) on commit
(via `scripts/clean_roadmap.py` + the pre-commit hook). The autonomous build is done; what's
left is new features and data-gated expansion.

---

## New data → real accuracy upside (MLB is the biggest slate)
- ◧ **P1** **Weather for MLB** — ingest + validated, model-use pending. `mlb_weather.py` (Open-Meteo, free) stores per-game temp/wind + a park-orientation **wind-out** component in `game_weather`; surfaced on MLB cards; `mlb_weather_features.py` injects it into `derived` and the keys are in the hits/TB/HR `FEATURE_KEYS`. Validated: wind out (≥5mph) → 65% over-rate vs 43% calm/in. *Last step:* run the `weather-backfill` GHA workflow, then retrain `total_bases_v1`/`hits_v1`/`mlb_home_runs_v1` (A/B-validate with `ab_compare` before promoting) and commit the models.
- ☐ **P2** **Confirmed lineups + batting order** — batting 1st vs 8th changes plate appearances → moves hits/TB/RBI props. Extend the starter scrape to order.
- ☐ **P2** **Umpire assignments** — home-plate ump K-zone tendency, a real edge for strikeout props.
- ☐ **P2** **Vegas game/team totals as a model feature** — live odds now flow; a high implied team total = more offense. Feed it into the MLB/NBA models.

## Data-gated (unlocks as games accrue; the feature-ideas digest flags when ready)
- ☐ **P1** Backfill depth for **NHL** (~11 games) and **WNBA** (~43) so prop models get signal and winner models become trainable.
- ☐ **P3** Train **NHL/WNBA winner models** once data is sufficient (WNBA first, basketball-generic, revisit ~150+ games).
- ☐ **P3** Extend the **model/market blend + soft-line finder to NHL/WNBA** — auto-tunes in once those have sharp-market coverage.
