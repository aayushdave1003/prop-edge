# prop-edge

An ML pipeline that predicts player prop outcomes across major US sports and identifies pricing inefficiencies against PrizePicks lines.

Research project. Not financial advice. Not a betting product.

## What it does

For every starting pitcher in tonight's MLB slate:

1. Pulls historical performance (3+ seasons of box scores)
2. Builds 84 lookahead-safe features per player-game
3. Predicts a strikeout count distribution using a LightGBM Poisson model
4. Compares the distribution to live PrizePicks lines
5. Flags picks with model edge of 5%+ and logs them for paper-tracking

A cron job pulls fresh PrizePicks lines hourly so backtests can run against closing-line data.

## What makes it different

- Lookahead discipline. Every rolling feature uses shift(1) before aggregating, verified by hand.
- Distribution-matched inference. Training and inference compute identical features through the same code path.
- Time-based splits. Train on games before 2025-01-01, test on 2025+.
- Calibration audit. Predicted probabilities bucketed against actual hit rates.
- Honest paper-tracking. Every pick logged with its line, model version, and timestamp.

## Current state

- ~217,000 MLB player-games (2023 through 2026 season)
- 84 features per row with verified lookahead protection
- Trained pitcher strikeouts model: 15% MAE improvement over baseline (1.80 vs 2.12 K per start, 2025+ holdout)
- Hourly PrizePicks scraper covering 20 stat types across MLB, NBA, NHL, WNBA
- Daily picks generation and paper-tracking pipeline

## Roadmap

- More models: batter hits, batter total bases
- Calibration recalibration via isotonic regression
- WNBA, NHL, NBA, NFL models as seasons align
- Correlation-aware parlay optimizer
- Streamlit dashboard

## Tech stack

Python 3.13, Postgres 16, LightGBM, pandas, scipy, SQLAlchemy, curl_cffi, structlog, tenacity, pydantic.
