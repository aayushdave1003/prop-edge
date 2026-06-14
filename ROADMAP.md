# prop-edge — Project Roadmap

Priorities: **P0** blocking/reliability · **P1** core value · **P2** quality/scale · **P3** future.
Status: ☐ todo · ◧ in progress · ✅ done.

The autonomous build is **complete** — the pipeline scrapes, predicts, **blends model + sharp market**, settles, self-heals, self-tunes its cutoffs/calibration, monitors, backtests, and reports unattended on GitHub Actions; human input is needed only for a paid-API top-up or a new feature. What's left is **new features** and **data-gated** expansion. Everything already shipped is summarized at the bottom.

---

## Open — new features (not data-gated; pick by value)

### New data → real accuracy upside (MLB is the biggest slate)
- ◧ **P1** **Weather for MLB** — INGEST + VALIDATED, model-use pending. `props/ingest/mlb_weather.py` (Open-Meteo, free, no key) stores per-game temp/wind/humidity + a park-orientation **wind-out** component in `game_weather` (migration 0011); wired into daily.sh, surfaced as a chip on MLB pick cards (💨 wind out / 🍃 in / 🏟️ dome). **Validated on 80 settled offense picks:** wind blowing out (≥5mph) → **65% over-rate vs 43% calm/in** and +1.26 vs +0.21 actual-minus-line — a real signal. **Retrain path set up:** `props/features/mlb_weather_features.py` injects `wx_temp`/`wx_wind_out` into `player_games.derived` (wired into daily.sh; 1,716 player-games populated so far), and the keys are added to the hits/total_bases/home_runs `FEATURE_KEYS`. A **`weather-backfill` GHA workflow** (dispatch, `days` input) runs the network-heavy Open-Meteo backfill + derived injection on GitHub's reliable network. *Last step (run after the backfill):* retrain `total_bases_v1` / `hits_v1` / `mlb_home_runs_v1` (they read the prod DB, no network) and commit the model files — converts the validated wind signal into model accuracy.
- ☐ **P2** **Confirmed lineups + batting order** — batting 1st vs 8th changes plate appearances → directly moves hits/TB/RBI props. Extend the existing starter scrape to order.
- ☐ **P2** **Umpire assignments** — home-plate ump K-zone tendency is a real edge for strikeout props.
- ☐ **P2** **Vegas game/team totals as a model feature** — live odds now flow; a high implied team total = more offense. Feed it into the MLB/NBA models.

### Model / analytics
- ☐ **P2** **Per-direction cutoffs** — tune over vs under independently (the MLB-hits 26pp over/under split proves asymmetry is real). Future-proofing; modest on today's data (the hot side is already captured).
- ☐ **P3** **Same-game *correlated* parlays** — the builder avoids negative correlation; add the upside version (stack a pitcher's Ks with the opposing offense's unders).
- ☐ **P3** **Prediction intervals** — show a confidence band, not just a point probability.
- ☐ **P2** **Model-drift auto-alert** — Discord ping when a model's live calibration degrades (the daily backtest already has the raw→recalibrated Brier).

### Product / UX
- ✅ **P2** **One-click "tail this slate"** — DONE: a "📋 Tail this slate" expander on Today's Picks shows the recommended picks + best 2-pick as a copyable `st.code` block (built-in copy button), formatted by the shared `notify.format_slate`.
- ✅ **P3** **Email push of the morning slate** — DONE: `props/utils/notify.send_email` (SMTP, free, optional) sends the recommended slate; wired into the morning digest alongside Discord (each fires independently if configured). Set `SMTP_USER`/`SMTP_PASSWORD`/`EMAIL_TO`. *(Paid SMS and Telegram were considered and declined.)*
- ☐ **P3** **Public results page** — shareable, read-only proof of record (67.8% rec-tier).
- ☐ **P3** **Historical pick browser** — filter settled picks by sport/stat/edge to explore.
- ☐ **P3** Dark/light toggle, historical-slate date picker.

### Ops / quality
- ☐ **P2** **A/B model shadow-logging** — run a candidate model alongside prod and compare without risk.
- ☐ **P2** Standardize structlog usage; add run/request IDs.
- ☐ **P1** Ensure every retrain logs a `backtest_runs` row (Performance tab already charts it).
- ☐ **P2** Compliance — keep "paper-tracking only, not betting advice" framing; add disclaimer/age-gate if this ever goes public.

## Open — data-gated (unlocks as games accrue; the feature-ideas digest flags when ready)
- ☐ **P1** Backfill depth for **NHL** (~11 games) and **WNBA** (~43) so prop models get signal and winner models become trainable.
- ☐ **P3** Train **NHL/WNBA winner models** once data is sufficient (WNBA first, basketball-generic, revisit ~150+ games).
- ☐ **P3** Extend the **model/market blend + soft-line finder to NHL/WNBA** — auto-tunes in once those have sharp-market coverage.

---

## ✅ Shipped

**Autonomous operations** — cloud scrape via residential proxy (PrizePicks blocks datacenter IPs); DST-safe cron gating; self-heal stranded picks; settle no longer false-voids late box scores (+ box-score cap 30→90); ingest monitor (line freshness, slate volume, box-score coverage, injury feed, **Odds API quota** alert); 0-picks/step-failure health ping; **daily walk-forward backtest** (rec-tier trend, calibration drift, cutoff-fit sweep); budgeted weekly `market_odds` refresh; nightly scorecard + cold-streak alert; daily feature-ideas digest.

**Models & accuracy** — per-category **auto-tuning cutoffs** (Wilson-LB vs 57.7% breakeven, with safer-slice step-up); isotonic calibration (10/15 models); **Platt recalibration** of live over-confidence; **per-sport model/market blend** (NBA leans market, MLB leans model; blended value stored as `model_prob`, raw + market kept); **availability / projected-minutes model** + teammate-out bump; correlation-aware diversified parlay; feature-leakage audit (+ regression test); holdout report; model-versioning doc.

**Data & market edge** — NBA box scores via ESPN (datacenter-safe); `line_open`/`line_movement` daily; injury status into picks; **live sharp odds (NBA+MLB)**; **soft-line finder** (PrizePicks vs sharp market, Poisson-implied); **CLV** (PrizePicks) + **sharp-market CLV** (vs DK/FD close); Odds API re-upped to 100k + safe key-rotation script + quota monitor.

**Pick generation** — centralized suppression rules (one documented module); injury/stale/DNP/line-band/over-confidence filters; per-pick "why"; dedup + sequence-drift fix.

**Product / UX** — visual redesign; **💰 Soft Lines tab**; sharp-CLV panel; Paper P&L + drawdown; ROI by parlay size; responsive (mobile) card grid; URL filter persistence; refresh button; Pacific-time display; Discord morning digest.

**Infra & code quality** — Docker/libgomp on Railway; batched `derived_writer` backfill (40min→27s) + TCP keepalives; tracked migration runner (0001–0010); inference out of the dashboard render path; secrets hygiene; unit suite; CI (byte-compile + **flake8 NameError-class gate** + tests); **pick-generation smoke test**; clean repo root.

---

*Notes:* the per-sport market-disagreement *filter* was shipped then **reverted** the same day — it keyed on `picks.market_edge`, which was the 0.5 neutral prior, not a real gap (lesson logged in memory: validate market signals against the `market_odds` table). The model/market blend is the correct version of that idea.
