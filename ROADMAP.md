# prop-edge — Project Roadmap

Priorities: **P0** blocking/reliability · **P1** core value · **P2** quality/scale · **P3** future.
Status: ☐ todo · ⧖ in progress · ✅ done.

Suggested execution order: **§1 (P0s) → §2/§3 (P1s) → §7 tests → §6 polish → P3 expansion.**

---

## 1. Production & Infrastructure
- ✅ **P0** Daily pipeline was dying on Railway connection drops during feature writes (row-by-row UPDATE of all ~28K `player_games.derived` in one ~40-min transaction over the proxy → "server closed the connection" → `pipefail` aborted before pick generation). Fixed: shared `derived_writer.py` (batched executemany, per-batch commit, retry, incremental by game_date) + TCP keepalives in `db.py`. Full backfill 40min→27s; daily writes 36K→~430 rows. *(2026-06-05)*
- ☐ **P0** Fix `libgomp.so.1` on Railway (verify nixpacks installs `libgomp1`, or move to a Dockerfile with `apt-get install libgomp1`). NBA preds + MLB inference (in the dashboard's live-render path) currently fail in prod.
- ☐ **P1** One-time: run a full derived backfill on prod (`DERIVED_BACKFILL_ALL=1`) to fill the 06-02/06-03 gaps, then re-run the daily pipeline for today.
- ☐ **P0** Move game-prediction inference out of the dashboard render into the cron; dashboard reads `games.context` only. Fixes the ~30s slow tab *and* the Railway crash.
- ☐ **P1** Replace the startup `ALTER TABLE … ADD COLUMN IF NOT EXISTS` hack in `dashboard.py` with a real migration (Alembic or versioned `sql/` migration run on deploy).
- ☐ **P1** Health-check / alerting: Discord ping if the daily cron fails or produces 0 picks.
- ☐ **P2** Centralize cron schedule + timezone handling (LA-time logic is scattered across queries).
- ☐ **P2** Secrets hygiene: confirm `.env` isn't committed; rotate keys if it ever was.

## 2. Model Quality & ML
- ☐ **P1** Diagnose the sub-breakeven win rate: per sport × stat × direction × edge-bucket, find the bleeders.
- ☑ **P1** Confidence threshold tuning — DONE: per-category cutoffs (`props/models/category_cutoffs.py` + `category_cutoffs.json`) auto-derived from settled history as the lowest `model_prob` whose Wilson-LB win rate clears the 57.7% breakeven. Live (MLB ≥0.55 / 64% hist; NBA suppressed — 52.8% coin-flip; WNBA/NHL default pending data). Dashboard recomputes from the DB every 6h; recompute the seed offline with `python -m props.models.category_cutoffs`.
- ☐ **P1** Calibration coverage: only some models have `_calibrator.pkl` (NBA pts/reb/ast yes; threes, MLB HR, all NHL, all WNBA no). Add isotonic calibration everywhere.
- ☐ **P2** Model versioning/registry: currently `*_v1`. Define retrain cadence, track metrics per version, add rollback path.
- ☐ **P2** Feature-leakage audit (confirm strict `< game_date` cutoffs in all rolling features).
- ☐ **P3** Train NHL/WNBA **winner** models once data is sufficient (WNBA first — basketball-generic; revisit ~150+ games). NHL currently 11 games, WNBA 43.
- ☐ **P3** Correlated-leg modeling for parlays (`build_parlays` dedups players but doesn't model correlation).

## 3. Data Pipeline & Coverage
- ☐ **P1** Backfill depth for NHL (11 games) and WNBA (43) so prop models have signal and winner models become trainable.
- ☐ **P1** Confirm `line_open`/`line_movement` populate daily for all sports (recently added).
- ☐ **P2** Ingest monitoring: per-table daily row-count deltas; alert on anomalies (missing slate, stale lines).
- ☐ **P2** Injury data: `injury_flag` is hardcoded `0` in the dashboard query — wire a real source or remove the dead UI path.
- ☐ **P3** Add sportsbooks beyond PrizePicks for line-shopping / consensus.

## 4. Pick Generation & Product
- ☐ **P1** Make suppression rules (>97% confidence, multi-game/combined-player filters) configurable + documented in one place.
- ☐ **P2** Bankroll/Kelly tracking: simulate a running paper bankroll from the Kelly sizes already shown.
- ☐ **P2** Per-pick "why" explanations (top features / form / line move) on the card.
- ☐ **P3** Morning Discord alert of top-edge picks.

## 5. Evaluation & Tracking
- ☐ **P1** Ensure every retrain logs a `backtest_runs` row (Performance tab already charts it).
- ☐ **P2** True out-of-sample holdout report per model (not just walk-forward).
- ☐ **P2** ROI by parlay size with realistic payouts, not just win rate.

## 6. UI / UX
- ✅ Visual redesign (Inter, gradients, glass surfaces, refined cards/tabs/metrics)
- ✅ Fixed raw-HTML code-block rendering bug (`_html` sanitizer)
- ✅ Form-dot ordering (oldest→recent) + push handling
- ✅ Graceful prediction errors (no raw tracebacks)
- ✅ WNBA + NHL market-implied game cards
- ✅ Card-height alignment
- ☐ **P2** Loading states/spinners for the slow tab (moot after §1 precompute).
- ☐ **P2** Mobile / narrow-screen layout (cards fixed at 3-per-row).
- ☐ **P3** Filter persistence, historical-slate date picker, dark/light toggle.

## 7. Code Quality & Observability
- ☐ **P1** Tests: add unit tests for pure logic (settle classification, edge/Kelly math, `_html` sanitizer, form-dot logic, moneyline de-vig).
- ☐ **P2** Clean repo root: `backtest_*.csv`, `*.log`, `backfill.log` sitting in the tree — gitignore or move to `logs/`/`artifacts/`.
- ☐ **P2** Standardize structlog usage; add run/request IDs.
- ☐ **P3** CI: lint + tests on push.

## 8. Compliance / Safety
- ☐ **P2** Keep "paper-tracking only, not betting advice" framing consistent; add disclaimer/age gate if this ever goes public.
