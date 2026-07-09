# prop-edge

A self-running ML pipeline that predicts player-prop outcomes across major US sports, prices them against PrizePicks lines, and **runs, settles, monitors, and self-corrects entirely on its own**.

Research project. Paper-tracking only. Not financial advice, not a betting product — it never places a bet or touches an account.

📊 Live dashboard: **[prop-edge-production-1b02.up.railway.app](https://prop-edge-production-1b02.up.railway.app)**

---

## What it does

Every morning, for MLB, NBA, WNBA, and NHL, the cloud pipeline:

1. **Scrapes** fresh PrizePicks lines (through a residential proxy so it runs on cloud IPs), plus schedules, box scores, injuries, and probable starters
2. **Builds** 110–137 lookahead-safe features per player-game (rolling form, matchup quality, platoon splits, basketball IQ, opponent defense, rest, park factors, …)
3. **Predicts** expected outcomes with LightGBM Poisson models (binary classifiers for sparse targets like home runs) and converts to over/under probabilities via the Poisson CDF
4. **Logs picks** with model edge, suppressing injured/out players, bench/DNP risks, and stale games at the source
5. **Settles** the prior night against final box scores (NBA box scores come via ESPN so they settle on cloud IPs where `nba_api` is blocked)
6. **Self-heals** anything stranded, **auto-tunes** its own confidence cutoffs from the latest results, and **reports** to Discord — a scorecard, cold-streak alerts, and a daily feature-ideas digest

No laptop required. It runs on GitHub Actions 24/7 and reaches out only when it genuinely needs a human (a paid API to top up, an upstream API that broke) — never as a silent failure.

---

## Results (late June 2026)

| | Sample | Win rate |
|---|--------|----------|
| **Recommended tier** (forward-only, valid-line, played) | n = 167 | **47.3%** · 95% CI [39.9%, 54.9%] |
| All logged picks (same gates) | 1,165W – 1,229L | 48.7% |

The **recommended tier** is the slate the system actually surfaces — picks clearing a per-category confidence cutoff auto-derived from settled history. It's measured **forward-only** (picks logged after game start are excluded), **valid-line-only** (picks with no prop line — nothing to be right or wrong about — are dropped), and **played-only** (a player who didn't suit up is a void PrizePicks refunds, not a win/loss), with each cutoff selected **point-in-time** (it sees only picks that settled *before* the pick it judges). A 2-pick PrizePicks parlay breaks even at **57.7%**; the recommended tier sits **below** breakeven and **no sport or category clears it** (WNBA 48.9%, MLB 39.3%) — so this is **not a proven edge**.

An earlier "72.1%" headline was an in-sample measurement artifact: the cutoff had been selected on the same outcomes it was then scored against. The full trail — 72.0% → 56.4% → 50.3% → ~47%, as lookahead, no-line, and DNP picks were removed — is reproduced in [`props/models/mirage_analysis_mlb_hits_under.py`](props/models/mirage_analysis_mlb_hits_under.py).

---

## Autonomous operations

The thing that makes prop-edge unusual isn't the models — it's that the whole system **operates itself**:

- **Cloud-native** — the full pipeline runs on **GitHub Actions** against a Railway Postgres DB. DST-safe scheduling fires it once a day regardless of GitHub's cron drift; the line scrape runs through a residential proxy because PrizePicks blocks datacenter IPs.
- **Self-healing** — if a transient failure strands picks on already-final games, an end-of-run step re-attempts box scores + settlement until they clear, and the settle path auto-voids truly-unrecoverable orphans. It pings Discord only when it actually fixes something.
- **Self-tuning** — per-category cutoffs (per sport, and per sport×stat where there's data) are recomputed from the live DB as picks settle: a stat that drifts below breakeven is **auto-suppressed**, and lifts itself once it proves out again. No manual retuning.
- **Self-monitoring** — an ingest monitor checks line freshness, slate volume, box-score coverage, and the injury feed, and alerts on anomalies before they zero out a slate.
- **Self-reporting** — a nightly **scorecard** (recommended-tier W/L vs breakeven, by sport, 7-day rolling, **top-5-picks daily accuracy**, cold-streak alert), a **closing-line-value** tracker, a **daily walk-forward backtest** (replays the recommended-tier strategy over a rolling window of settled picks — win rate vs breakeven + trend, model calibration/Brier + drift, and a counterfactual cutoff sweep that audits the auto-tuner), and a **daily feature-ideas digest** that surfaces data-driven opportunities to build next.

---

## Models (27 active)

Poisson regression per stat (binary classifier for home runs). Combo stats (NBA/WNBA PRA·P+R·P+A·R+A, MLB hits+runs+RBIs) are **direct summed-target models** — they beat summing component lambdas. Isotonic calibration on top, recalibrated on full regular-season data and excluding playoffs (a different distribution).

| Sport | Models |
|-------|--------|
| **MLB** | pitcher strikeouts, **batter strikeouts**, hits, RBIs, total bases, home runs, **hits+runs+RBIs**, **earned runs allowed**, **hits allowed** |
| **NBA** | points, rebounds, assists, threes, **+ 4 combos (PRA, P+R, P+A, R+A)**, winner model |
| **WNBA** | points, rebounds, assists, **+ 4 combos** |
| **NHL** | goals, assists, saves |

New markets earn their slot only if they beat a season-average baseline out-of-sample — pitcher ER/hits-allowed (+8% / +18% MAE) and the combos shipped; low-frequency batter events (runs, walks, doubles, steals, singles) were assessed and **dropped** as un-modelable noise. NBA/MLB also have game-winner models; NHL/WNBA winner models become trainable as history accrues.

---

## Data

| Sport | Player-games | Derived features |
|-------|-------------|------------------|
| MLB   | 228,000+ | 137 |
| NBA   | 36,000+  | 120 |
| WNBA  | 9,700+   | 135 |
| NHL   | 58,000+  | 114 |

The PrizePicks scraper covers 40+ stat types across all four sports.

---

## Feature engineering

**MLB (137)** — rolling form (5/10/20/season) for all batting & pitching stats · opposing-pitcher quality (ERA, K/9, H/9, BB/9, HR/9) · batter-vs-pitcher history · platoon splits · opposing-lineup quality · park factors · **ballpark weather** (temp/wind, with a park-orientation wind-out component — wind blowing out hit 65% overs vs 43% calm/in) · advanced metrics (BABIP, ISO, K%, BB%, hard-contact) · rest.

**NBA (120)** — rolling box-score form · basketball IQ (usage, spacing, foul-drawing, paint scoring, AST/PTS, pts/FGA) · play-type distribution (iso/PnR/spot-up) · opponent positional defense (pace-adjusted) · teammate-absence usage bump · home/away & back-to-back splits · playoff/series context · market over-prob.

**WNBA (135)** — the NBA feature set adapted: rolling stats, basketball IQ, opponent positional defense, career-vs-opponent, close-game rate.

**NHL (114)** — rolling form for goals/assists/points/shots/hits/blocks/PP stats · goalie features (save% trends, shots against, workload, GAA) · special teams · faceoffs · rest.

---

## Quality controls

- **Lookahead discipline** — every rolling feature uses `shift(1)` before aggregating; a regression test fails if a `shift(1)` is ever dropped. Time-based train/test splits, never random.
- **Per-category cutoffs** — recommended picks must clear a cutoff auto-derived (Wilson lower bound vs breakeven) per sport/stat; confidently-losing buckets are auto-suppressed.
- **Injury suppression** — picks aren't logged for players currently Out / Doubtful / IL.
- **Availability / projected minutes** — a recency-weighted minutes projection (last-5 > last-10 > last-20) drops likely-DNP basketball picks and minutes that are collapsing out of the rotation, without over-suppressing returnees (a teammate-out bump keeps bench players who'll absorb minutes). Plus scratched-pitcher voids. All suppression rules live in one documented module.
- **Stale-game & orphan handling** — never logs picks for already-played games; settlement auto-voids picks whose line was pruned or whose game never went final.
- **Correlation-aware parlays** — the suggested slate never stacks two legs from the same game in the same direction (the cluster that busts together).
- **Model/market blend** — the stored probability is a per-sport blend of the model and the no-vig market (`w·model + (1−w)·market`), with weights self-tuned from settled results: **NBA leans on the market** (deep, efficient) and **MLB leans on the model** (softer market). The market line is a **median no-vig consensus across 6 books** (DK, FD, BetMGM, Caesars, BetRivers, BetOnline) — robust to any one book shading, and ~70% more props get a true line than DK+FD alone. It only blends against a real line — never a neutral prior. Cut the daily Brier 0.245→0.235.
- **Calibration** — isotonic per model, refit on regular-season data, plus a self-tuning **Platt recalibration** learned from settled paper results that corrects residual over-confidence; it sizes Kelly and the shown confidence on the corrected probability. Closing-line-value tracked as the long-run edge signal.

---

## Architecture

```
Data sources:  PrizePicks (via residential proxy) · MLB Stats API · ESPN · nba_api
        │
   Ingest:  schedules · box scores (NBA→ESPN on cloud) · injuries · prop lines
        │
  Features:  rolling form · matchup quality · advanced IQ  (110–137 / player-game)
        │
   Models:  17 LightGBM (Poisson + binary) + isotonic calibration + winner models
        │
    Picks:  per-category edge cutoffs · injury/bench/stale filters · dedup · logging
        │
  Operate:  settle · self-heal · auto-tune cutoffs · monitor · scorecard · alerts
        │
   Output:  PostgreSQL · Streamlit dashboard · Discord
```

### Deployment
- **GitHub Actions** runs the full pipeline (`scripts/daily.sh`) + intraday refreshes against the Railway DB — DST-safe, retry-resilient, self-healing.
- **Railway** hosts PostgreSQL + the Streamlit dashboard (Docker build, auto-deploys on push to `main`).
- The PrizePicks scrape routes through a residential proxy (`PRIZEPICKS_PROXY`); a local Mac cron is an optional backup, not a dependency.
- Schema changes go through a tracked migration runner (`props/maintenance/migrate.py`).

---

## Dashboard

PrizePicks-style cards: player photo + team logo, line/direction/confidence, **per-pick "why"** (form + market edge + line movement), form dots, Kelly sizing, injury-status badge, line-movement signal, live in-game tracker, combo cards. Every pick is shown, with the **recommended** ones (clearing their category cutoff) **⭐ starred** and sorted first. A **🔄 Refresh picks** button re-reads the DB on demand so a slate logged after you opened the page (NBA/WNBA picks often land after MLB) shows up without waiting on the cache.

Performance tab: win rate vs the 57.7% breakeven, recommended-tier proof, **active confidence cutoffs**, **closing line value** (incl. **sharp-market CLV** vs the multi-book no-vig close), **ROI by parlay size**, paper P&L, **daily walk-forward backtest** (rec-tier win-rate trend, Brier, and cutoff-fit findings), calibration, win rate by stat × direction.

A **💰 Soft Lines** tab ranks tonight's PrizePicks props the sharp market prices as +EV — independent of the model. It recovers the sharp book's implied projection (Poisson mean from its no-vig prob) and re-prices it at the PrizePicks line; a side clearing the 57.7% breakeven is a soft line. A **📋 Tail this slate** box exports the recommended picks as copyable text, and the morning slate can also be **emailed** (free SMTP push) alongside the Discord digest.

---

## Quickstart

Prerequisites: Python 3.13+, PostgreSQL 16.

```bash
git clone https://github.com/aayushdave1003/prop-edge.git
cd prop-edge
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
createdb props && psql props < sql/schema.sql
python -m props.maintenance.migrate     # apply schema migrations
```

Run the daily pipeline (everything: ingest → predict → log → settle → self-heal → report):

```bash
bash scripts/daily.sh
```

Run the test suite:

```bash
pytest tests/ -q
```

Key env vars (`.env`): `DATABASE_URL`, `RAILWAY_DATABASE_URL`, `DISCORD_WEBHOOK_URL`, `ODDS_API_KEY`, `PRIZEPICKS_PROXY`.

---

## Roadmap

See **[ROADMAP.md](ROADMAP.md)** for the full, continuously-updated list. The build of the autonomous system is done, and the high-value model work is largely exhausted — new signal features (team defense, times-through-order, pitcher velo, …) have been assessed and shown neutral on the now-saturated models, and the prop-market lane is built out. What's left is data-gated (NHL/WNBA market blend as sharp coverage accrues) or seasonal: a **college-basketball foundation is built and parked for tip-off in November**.

---

## Tech stack

Python 3.13 · PostgreSQL 16 · LightGBM · scikit-learn · pandas · scipy · SQLAlchemy 2 · Streamlit · structlog · tenacity · nba_api · curl_cffi · pydantic · GitHub Actions · Railway.

---

## A note on intent

prop-edge paper-tracks picks against publicly visible PrizePicks lines. It places no bets, touches no accounts, and offers no advice. It's a research artifact demonstrating end-to-end, self-operating ML on sports data.
