# Changelog

Auto-archived from ROADMAP.md as items ship.

## Shipped — 2026-06-14
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
