# Changelog

Auto-archived from ROADMAP.md as items ship.

## Shipped — 2026-06-15
- ✅ **P2** **Roster sync** — DONE: `props.ingest.rosters` sets `current_team_id` from each league's official current roster (MLB/NHL by native id, NBA/WNBA by normalized name — basketball isn't keyed on ESPN ids). Update-only (never clears; the lookup's recency filter hides departed players), runs daily. Matched 1259/109/515/201 players across MLB/NHL/NBA/WNBA, corrected 68 who'd moved teams.

## Shipped — 2026-06-14
- ✅ **P3** **DB backup / restore** — DONE: `db_backup.yml` nightly `pg_dump` (custom-format, version-matched via postgres:16) → 30-day retained artifact, with a size sanity-check + Discord alert on failure; `scripts/restore_db.sh` is the guarded, tested restore path (restore into a scratch DB to verify a snapshot).
- ✅ **P3** **Type checking in CI** — DONE: scoped `mypy` gate (`props/utils` + `props/ops`, pydantic plugin) wired into `ci.yml` next to the flake8 NameError gate; `[tool.mypy]` grows by adding packages as they're cleaned.
- ✅ **P2** **Cost / usage dashboard** — DONE: `props.ops.usage` snapshots Odds API credits, scrape volume, pipeline freshness, and DB size/biggest-tables in one view — CLI + an Ops view on the dashboard (`?view=ops`) + logged in the daily run. Railway $ isn't API-metered, so DB size is the proxy + a link out. *(Immediately caught the June 4–14 pick outage.)*
- ✅ **P3** **Dashboard perf/latency monitoring** — DONE: `props.ops.dashboard_monitor` times `/_stcore/health` AND a real render, pinging Discord when the app is down or render latency blows past the threshold; runs in the daily pipeline and on-demand from the Ops view.
- ✅ **P2** **Automated weekly retrain pipeline** — DONE: `props.models.retrain_and_promote` retrains each MLB offense model, A/B-gates it vs prod on recent settled games, and promotes only winners (≥0.5% MAE) — then recalibrates; regressions stay prod. `weekly_retrain.yml` runs it Mondays (DST-gated) and commits promoted models so they deploy. Decisions log to `backtest_runs` + Discord.
- ✅ **P2** **Offline feature-eval harness** — DONE: `props.models.feature_eval` scores a candidate feature on settled data via its association with model *residual* (the real "does it add signal the model misses" test), with coverage + tercile monotonicity and a retrain verdict. Generalizes the weather/lineup pattern; pairs with `ab_compare`.
- ✅ **P1** **Weather for MLB** — DONE: Open-Meteo ingest + park wind-out component (`game_weather`, surfaced on cards), validated (wind out ≥5mph → 65% over vs 43% calm), injected into `derived` (978 games backfilled). Retrained with the A/B gate: **total_bases (+1.5% MAE) and hits (+2.6%) promoted**; home_runs **rejected** (−7.7% — weather hurt the sparse HR classifier, keys removed). Recalibrated both. *(Coverage is partial/recent-skewed — re-running the budgeted backfill over time will lift it and a future retrain can recheck.)*
- ✅ **P2** **Player detail page** — DONE: `?player=<name>` (or the sidebar lookup) renders a player's full record (overall W/L, by stat×direction, recent picks); shareable + stops.
- ✅ **P3** **Line-movement / steam alerts** — DONE: `capture_sharp_close` Discord-pings picks where the sharp prob moved ≥8pp toward (confirmation) or against (caution) our side since pick time, each intraday refresh.
- ✅ **P3** **Discord slash-command bot** — DONE: `props/bot/discord_interactions.py` (FastAPI, signature-verified) serves `/picks` `/record` `/player` from the DB; `register_commands.py` + `requirements-bot.txt` + deploy notes — runs as a separate Railway service (read-only).
- ✅ **P3** **Player watchlist** — DONE: a sidebar multiselect (persisted in `?watch=`) follows players; their picks surface in a pinned "Watchlist" panel on Today's Picks.
- ✅ **P3** **Stat-bucket leaderboard** — DONE: the Performance tab shows the hottest + coldest sport×stat×direction buckets (min 8 settled) — which edges are live, which faded.
- ✅ **P3** **PWA-lite / mobile polish** — DONE: home-screen meta injected into the parent `<head>` (add-to-home-screen capable) on top of the responsive card grid. (Full offline PWA would need a served manifest + service worker.)
- ✅ **P2** **Per-direction cutoffs** — DONE: `category_cutoffs` tunes a third level `sport|stat|direction` above `sport|stat` (a stat can perform very differently over vs under). `rec_cutoff` checks dir → stat → sport → default; all callers pass direction. On prod: MLB hits UNDER gets its own 0.55 cutoff (84%, n=94, captures all) while total_bases-under / nba-points-over suppress per-direction; falls back to the stat level where a direction lacks the sample.
- ✅ **P3** **Same-game correlated parlays** — DONE: a "🔗 Correlated stacks" section pairs a pitcher's strikeouts OVER with an opposing-team batter UNDER in the same game (positively correlated — a dominant pitcher suppresses the opposing offense, so the legs hit/miss together), ranked by correlation-bumped joint probability.
- ✅ **P3** **Prediction intervals** — DONE: each card shows a confidence band, not just a point prob — the model's `predicted_mean` (Poisson rate) → a 25–75% "likely" range (e.g. "Projection 6.2 · likely 4–8").
- ✅ **P2** **Model-drift auto-alert** — DONE: the daily backtest digest flags a sport whose recent calibration gap worsened materially vs its earlier window (>8pp worse and >10pp gap) — the model degrading there, likely needs a retrain.
- ✅ **P2** **One-click "tail this slate"** — DONE: a "📋 Tail this slate" expander on Today's Picks shows the recommended picks + best 2-pick as a copyable `st.code` block (built-in copy button), formatted by the shared `notify.format_slate`.
- ✅ **P3** **Email push of the morning slate** — DONE: `props/utils/notify.send_email` (SMTP, free, optional) sends the recommended slate; wired into the morning digest alongside Discord (each fires independently if configured). Set `SMTP_USER`/`SMTP_PASSWORD`/`EMAIL_TO`. *(Paid SMS and Telegram were considered and declined.)*
- ✅ **P3** **Public results page** — DONE: `?view=results` renders a clean, read-only shareable record (recommended-tier W/L + win% vs the 57.7% breakeven, overall, and per-sport) and stops — a link you can share as proof of record. Sidebar shows the share link.
- ✅ **P3** **Historical pick browser** — DONE: the Recent Picks tab gained sport/stat/direction/result filters (up to 60 days) with a live settled-record summary for the current filter.
- ✅ **P3** **Dark/light toggle + historical-slate date picker** — DONE: a sidebar ☀️ Light-mode toggle (persisted in `?theme=`, overrides the design tokens) and a Date selector in the browser to jump to any specific past slate.
- ✅ **P2** **A/B model comparison** — DONE: `props/models/ab_compare.py` scores a candidate model against the live model on recent settled games (read-only, never touches picks) and reports per-model MAE — so a retrain (e.g. the weather one) is validated before promotion; `--log` records it to `backtest_runs` (trigger `ab:<stat>`). Self-tested on 12,887 player-games.
- ✅ **P2** **structlog + run IDs** — DONE: `configure_logging` adds `merge_contextvars` and binds a per-run id (`bind_run_id`) onto every log line, so one run's output is correlatable.
- ✅ **P1** **Retrain logs a `backtest_runs` row** — DONE: `props/models/retrain_log.log_retrain_run` writes a row (trigger `retrain:<model>`, MAE/log-loss improvement vs baseline; migration 0012 adds `mae_improvement_pct`) — wired into total_bases / hits / home_runs `main()`.
- ✅ **P2** **Compliance** — DONE: a persistent research/paper-tracking disclaimer banner on the dashboard (no bets placed, 21+, hypothetical results), consistent with the footer + the public results page framing.


## Shipped — summary (through 2026-06-14)

**Autonomous operations** — cloud scrape via residential proxy; DST-safe cron gating; self-heal stranded picks; settle no longer false-voids late box scores (+ box-score cap 30→90); ingest monitor (line freshness, slate volume, box-score coverage, injury feed, Odds API quota alert); 0-picks/step-failure health ping; daily walk-forward backtest; budgeted weekly market_odds refresh; nightly scorecard + cold-streak alert; daily feature-ideas digest.

**Models & accuracy** — per-category auto-tuning cutoffs (Wilson-LB vs 57.7% breakeven, safer-slice step-up, per-direction); isotonic calibration (10/15 models); Platt recalibration; per-sport model/market blend (blended value stored as model_prob); availability/projected-minutes model + teammate-out bump; correlation-aware diversified parlay + correlated same-game stacks; prediction intervals; model-drift alert; feature-leakage audit; holdout report; model-versioning doc; A/B model comparison.

**Data & market edge** — NBA box scores via ESPN; line_open/line_movement daily; injury status into picks; live sharp odds (NBA+MLB); soft-line finder; CLV + sharp-market CLV; Odds API re-upped to 100k + safe key-rotation + quota monitor; MLB ballpark weather.

**Pick generation** — centralized suppression rules; injury/stale/DNP/line-band/over-confidence filters; per-pick "why"; dedup + sequence-drift fix.

**Product / UX** — visual redesign; Soft Lines tab; sharp-CLV panel; Paper P&L + drawdown; ROI by parlay size; responsive mobile grid; URL filter persistence; refresh button; Pacific-time display; Discord morning digest + email push; tail-this-slate; public results page; historical pick browser + date picker; light mode; compliance banner.

**Infra & code quality** — Docker/libgomp on Railway; batched derived_writer backfill + TCP keepalives; tracked migration runner (0001–0012); inference out of the render path; secrets hygiene; unit suite; CI (byte-compile + flake8 NameError gate + tests); pick-generation smoke test; structlog run IDs; retrain → backtest_runs logging; clean repo root.

*Note:* the per-sport market-disagreement filter was shipped then reverted same-day — it keyed on picks.market_edge (the 0.5 neutral prior), not a real gap. The model/market blend is the correct version (lesson: validate market signals against the market_odds table).
