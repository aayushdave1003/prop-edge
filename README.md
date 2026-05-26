# prop-edge

An ML pipeline that predicts player prop outcomes across major US sports and identifies pricing inefficiencies against PrizePicks lines.

Research project. Not financial advice. Not a betting product.

## What it does

Every day, for MLB (and soon NBA), this system:

1. Pulls fresh box scores and probable starting pitchers/lineups
2. Builds 80+ lookahead-safe features per player-game (rolling form, matchup quality, days rest, etc.)
3. Predicts expected outcomes using LightGBM Poisson models with isotonic regression calibration
4. Compares predicted distributions to live PrizePicks lines
5. Flags picks with model edge >= 5% and logs them for paper-tracking
6. Settles picks against actual outcomes the next morning

A cron job pulls fresh PrizePicks lines hourly so backtests run against real closing-line data.

## Headline result (day 1)

**Hits UNDER picks: 75 wins, 15 losses, 0 pushes (83.3%)** on 90 settled picks.

OVER picks on hits: 46-33 (58.2%) — also above breakeven.

One-day sample. Statistically significant (+6 standard deviations from chance) but not yet validated across multiple market conditions. Continuing to accumulate evidence nightly.

## What makes it different

- **Lookahead discipline.** Every rolling feature uses shift(1) before aggregating. Verified by hand on Shohei Ohtani's complete game history; values in the database match values computed at inference to the fourth decimal.
- **Distribution-matched inference.** Training and inference paths compute identical features through the same code. No quiet distribution shift between train and predict.
- **Time-based splits.** Train on games before 2025-01-01, test on 2025+. No random splits.
- **Calibration recalibration.** Isotonic regression layer on top of the hits model corrects Poisson over/underconfidence at each prop line. Predicted probabilities now match empirical hit rates within 1% on the 2025 holdout.
- **Honest "don't ship" decisions.** Trained Total Bases and RBI models. Both showed bad calibration on the 2025 holdout. Neither was registered to production. Discipline about not polluting paper-tracking matters more than model count.
- **Honest paper-tracking.** Every pick logged with the line, model version, snapshot timestamp. Settlement script computes real win/loss.

## Current state

### Data
- 217,890 MLB player-games (2023 through current 2026 season)
- 84 features per row with verified lookahead protection
- ~1,000 NBA player-games and growing (full 2025-26 season backfill in progress)
- Hourly PrizePicks scraper covering 40+ stat types across MLB and NBA

### Models deployed
- **MLB pitcher strikeouts (v1)** — LightGBM Poisson, 15% MAE improvement over baseline (1.80 vs 2.12 K per start)
- **MLB batter hits (v1)** — LightGBM Poisson + isotonic calibration, 5% MAE improvement, **83.3% win rate on first day of UNDER picks**

### Models trained but not deployed
- **MLB total bases** — 0.04% MAE improvement (effectively none), bad calibration. Failure cause: HR-driven overdispersion incompatible with Poisson.
- **MLB RBIs** — negative MAE improvement, bad calibration. Failure cause: RBI is team-state dependent (runners on base), not individual-driven.

### Models in training
- NBA points, rebounds, assists (v1) — pending backfill completion

### Pipeline
- Daily picks generation across all deployed models
- Settlement script that closes the loop on yesterday's picks
- Multi-model registry (clean addition pattern for new stats)
- Duplicate pick protection (unique index per player+line+date)

## Quickstart

Prerequisites: Python 3.11+, Postgres 16.

    brew install python@3.12 postgresql@16
    brew services start postgresql@16
    git clone https://github.com/aayushdave1003/prop-edge.git
    cd prop-edge
    python -m venv .venv && source .venv/bin/activate
    pip install -e ".[dev]"
    createdb props
    psql props < sql/schema.sql

Backfill MLB and compute features:

    python scripts/backfill_mlb.py
    python -m props.features.mlb_rolling
    python -m props.features.mlb_opposing_pitcher
    python -m props.features.mlb_opposing_lineup
    python -m props.models.strikeouts_v1
    python -m props.models.hits_v1
    python -m props.models.calibrate_hits_v1

## Daily ritual

    python -m props.ingest.mlb_schedule
    python -m props.ingest.mlb_boxscores
    python -m props.picks.settle_picks
    python -m props.picks.log_picks

## Roadmap

- NBA Finals coverage (live by June 4)
- Total bases and RBI v2 with Negative Binomial regression (Poisson failed for both due to overdispersion / team-context dependence)
- WNBA and NHL ingestion as their seasons align
- Correlation-aware parlay optimizer (where the real edge in PrizePicks may live)
- Streamlit dashboard for live picks, calibration plots, and cumulative ROI

## Tech stack

Python 3.13, Postgres 16, LightGBM, scikit-learn (isotonic regression), pandas, scipy, SQLAlchemy, curl_cffi (TLS-impersonated scraping), nba_api, structlog, tenacity, pydantic.

## A note on legality and intent

This project paper-tracks picks against publicly visible PrizePicks lines. It does not place real bets, does not interact with sportsbook accounts, and does not offer advice. It exists as a research artifact demonstrating end-to-end ML methodology.
