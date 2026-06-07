# prop-edge — Project Roadmap

Priorities: **P0** blocking/reliability · **P1** core value · **P2** quality/scale · **P3** future.
Status: ☐ todo · ⧖ in progress · ✅ done.

Suggested execution order: **§1 (P0s) → §2/§3 (P1s) → §7 tests → §6 polish → P3 expansion.**

---

## 1. Production & Infrastructure
- ✅ **P0** Daily pipeline was dying on Railway connection drops during feature writes (row-by-row UPDATE of all ~28K `player_games.derived` in one ~40-min transaction over the proxy → "server closed the connection" → `pipefail` aborted before pick generation). Fixed: shared `derived_writer.py` (batched executemany, per-batch commit, retry, incremental by game_date) + TCP keepalives in `db.py`. Full backfill 40min→27s; daily writes 36K→~430 rows. *(2026-06-05)*
- ☑ **P0** `libgomp.so.1` on Railway — VERIFIED working (2026-06-07): Railway builds with **Docker** (build logs: `FROM python:3.13-slim-bookworm`; step `[2/6] apt-get install … libgomp1`), not Nixpacks (stray `Procfile` is ignored). Debian glibc base resolves the lib the Nix linker couldn't. Corroborated: the GitHub Actions pipeline runs LightGBM daily on the identical stack (Debian + apt libgomp1 + same `requirements.txt`) and generates picks. Service Online, `/` and `/_stcore/health` → 200, no 5xx. Added a cached startup self-check in `dashboard.py` that logs `native deps OK — lightgbm X.Y` on first session for ongoing observability.
- ☐ **P1** One-time: run a full derived backfill on prod (`DERIVED_BACKFILL_ALL=1`) to fill the 06-02/06-03 gaps, then re-run the daily pipeline for today.
- ☑ **P0** Game-prediction inference out of the dashboard render — DONE: both NBA and MLB tabs now read `games.context` only (the cron persists win prob, margin, and probable pitchers via `persist_game_context`). Removed the MLB tab's live schedule fetch + `predict_mlb_games` LightGBM call — no inference on render, so no slow tab and no libgomp risk in the web path.
- ☐ **P1** Replace the startup `ALTER TABLE … ADD COLUMN IF NOT EXISTS` hack in `dashboard.py` with a real migration (Alembic or versioned `sql/` migration run on deploy).
- ☐ **P1** Health-check / alerting: Discord ping if the daily cron fails or produces 0 picks.
- ☑ **P2** Cron DST drift — DONE: `daily.yml`/`refresh.yml` trigger at both PDT & PST UTC times and gate execution to the intended Pacific hour, so jobs fire once year-round (not an hour off in winter).
- ☐ **P2** Secrets hygiene: confirm `.env` isn't committed; rotate keys if it ever was.

## 2. Model Quality & ML
- ☐ **P1** Diagnose the sub-breakeven win rate: per sport × stat × direction × edge-bucket, find the bleeders.
- ☑ **P1** Confidence threshold tuning — DONE: per-category cutoffs (`props/models/category_cutoffs.py` + `category_cutoffs.json`) auto-derived from settled history as the lowest `model_prob` whose Wilson-LB win rate clears the 57.7% breakeven. Dashboard recomputes from the DB every 6h; recompute the seed offline with `python -m props.models.category_cutoffs`. (MLB ≥0.55 / 64% hist; NBA tuned ≥0.725 once it had enough settled data — see below.)
- ☐ **P1** Calibration coverage: only some models have `_calibrator.pkl` (NBA pts/reb/ast yes; threes, MLB HR, all NHL, all WNBA no). Add isotonic calibration everywhere.
- ☑ **P2** Model versioning/registry — DONE (lightweight): `docs/MODEL_VERSIONING.md` defines naming, when to bump `v{N}`, evidence-driven retrain cadence (tied to `holdout_report`), the live cutoff guardrail, and one-commit rollback.
- ☐ **P2** Feature-leakage audit (confirm strict `< game_date` cutoffs in all rolling features).
- ☐ **P3** Train NHL/WNBA **winner** models once data is sufficient (WNBA first — basketball-generic; revisit ~150+ games). NHL currently 11 games, WNBA 43.
- ☐ **P3** Correlated-leg modeling for parlays (`build_parlays` dedups players but doesn't model correlation).

## 3. Data Pipeline & Coverage
- ☑ **P1** NBA box scores on datacenter — DONE: `props/ingest/nba_boxscores.py` rewritten to fetch via ESPN (stats.nba.com blocks cloud IPs, so on GitHub Actions NBA picks never settled). Maps stats by ESPN's `keys` array, resolves players by fuzzy name (similarity>0.8, same as PrizePicks) so box-score `player_id` matches the pick's, resolves the ESPN event from an `espn_` id or by date+team, and flips stale `live`/`scheduled` games to `final` from ESPN's status. Verified: settled all 38 stuck NBA Finals picks; NBA settled history 125→173, which let the cutoff tuner un-suppress NBA.
- ☐ **P1** Backfill depth for NHL (11 games) and WNBA (43) so prop models have signal and winner models become trainable.
- ☐ **P1** Confirm `line_open`/`line_movement` populate daily for all sports (recently added).
- ☐ **P2** Ingest monitoring: per-table daily row-count deltas; alert on anomalies (missing slate, stale lines).
- ☑ **P2** Injury data wired into picks — DONE: (1) the role-expansion `injury_flag` (teammate-out minutes from `detect_injury_expansion`) is now persisted on each pick (migration `0003`, stored by `log_picks`) instead of hardcoded `0`; (2) each card shows the player's OWN injury status (Out / *-IL / Day-To-Day) via a LATERAL name-join to `player_injuries` (81% exact-name match, fail-safe — a miss just shows no badge). Red "🚫" for won't-play statuses, yellow "⚠" for day-to-day.
- ☐ **P3** Add sportsbooks beyond PrizePicks for line-shopping / consensus.

## 4. Pick Generation & Product
- ☐ **P1** Make suppression rules (>97% confidence, multi-game/combined-player filters) configurable + documented in one place.
- ☐ **P2** Bankroll/Kelly tracking: simulate a running paper bankroll from the Kelly sizes already shown.
- ☑ **P2** Per-pick "why" — DONE: each card shows a synthesized rationale line (recent form / market edge / line movement).
- ☑ **P3** Morning Discord digest — DONE: the daily digest posts the recommended slate using the per-category cutoffs (not a flat 0.70).

## 5. Evaluation & Tracking
- ☐ **P1** Ensure every retrain logs a `backtest_runs` row (Performance tab already charts it).
- ☑ **P2** Out-of-sample holdout/calibration report — DONE: `python -m props.models.holdout_report` (per sport×stat win rate, predicted-vs-realized calibration + weighted MAE, recent-vs-earlier drift).
- ☑ **P2** ROI by parlay size — DONE: Performance tab shows 2/3/4-pick power-play ROI at the recommended-tier per-leg win rate.

## 6. UI / UX
- ✅ Visual redesign (Inter, gradients, glass surfaces, refined cards/tabs/metrics)
- ✅ Fixed raw-HTML code-block rendering bug (`_html` sanitizer)
- ✅ Form-dot ordering (oldest→recent) + push handling
- ✅ Graceful prediction errors (no raw tracebacks)
- ✅ WNBA + NHL market-implied game cards
- ✅ Card-height alignment
- ☐ **P2** Loading states/spinners for the slow tab (moot after §1 precompute).
- ☐ **P2** Mobile / narrow-screen layout (cards fixed at 3-per-row).
- ☐ **P3** Historical-slate date picker, dark/light toggle. *(filter persistence DONE — Today's Picks filters now persist in URL query params.)*

## 7. Code Quality & Observability
- ☑ **P1** Tests — DONE: 20 unit tests (settle, de-vig, `_html`, form dots, derived-writer guard, per-category cutoffs, ESPN stat parsing). Run on every push via CI.
- ☐ **P2** Clean repo root: `backtest_*.csv`, `*.log`, `backfill.log` sitting in the tree — gitignore or move to `logs/`/`artifacts/`.
- ☐ **P2** Standardize structlog usage; add run/request IDs.
- ☑ **P3** CI on push — DONE: `.github/workflows/ci.yml` byte-compiles + runs the 20-test suite on push/PR to main.

## 8. Compliance / Safety
- ☐ **P2** Keep "paper-tracking only, not betting advice" framing consistent; add disclaimer/age gate if this ever goes public.
