# prop-edge

An ML pipeline that predicts player prop outcomes across major US sports and identifies pricing inefficiencies against PrizePicks lines.

Research project. Not financial advice. Not a betting product.

---

## What it does

Every morning at 7am, for MLB, NBA, WNBA, and NHL, the system:

1. Pulls fresh box scores, schedules, and probable starting lineups
2. Builds 120–137 lookahead-safe features per player-game (rolling form, matchup quality, platoon splits, basketball IQ, opponent defense, rest, park factors, and more)
3. Predicts expected outcomes using LightGBM Poisson models (or binary classifiers for sparse targets like home runs)
4. Compares predicted distributions to live PrizePicks lines via Poisson CDF
5. Flags picks with model edge ≥ 5% and logs them for paper-tracking
6. Settles picks against final box scores the next morning
7. Posts top picks to Discord

A cron also scrapes PrizePicks lines hourly so backtests always run against real closing-line data.

---

## Live results (May 25 – June 4, 2026)

| Sport | Record | Win rate |
|-------|--------|----------|
| MLB   | 183W – 106L | **63.3%** |
| NBA   | 75W – 71L | 51.4% |
| **All** | **258W – 177L** | **59.3%** |

612 picks logged. 2-pick PrizePicks parlay breakeven is 57.7%. WNBA and NHL models deployed June 4 — settling soon.

---

## Models deployed (11 active)

### MLB
| Model | Type | Improvement |
|-------|------|-------------|
| `strikeouts_v1` | Poisson | +15% MAE vs baseline |
| `hits_v1` | Poisson + isotonic calibration | +5% MAE |
| `rbis_v1` | Poisson | +2.3% MAE |
| `total_bases_v1` | Poisson | +0.3% MAE |
| `mlb_home_runs_v1` | **Binary classifier** | AUC 0.58, +0.95% log-loss |

### NBA
| Model | Type | Improvement |
|-------|------|-------------|
| `nba_points_v1` | Poisson | +5.5% MAE |
| `nba_rebounds_v1` | Poisson | +4.3% MAE |
| `nba_assists_v1` | Poisson | +2.8% MAE |
| `nba_threes_made_v1` | Poisson | +0.2% MAE |
| NBA combo stats (`pts_rebs_asts`, `pts_rebs`, `pts_asts`, `rebs_asts`) | Derived (sum of component lambdas) | — |

### WNBA
| Model | Type | Improvement |
|-------|------|-------------|
| `wnba_points_v1` | Poisson | +4.6% MAE |
| `wnba_rebounds_v1` | Poisson | — |
| `wnba_assists_v1` | Poisson | +6.9% MAE |

### NHL
| Model | Type | Improvement |
|-------|------|-------------|
| `nhl_goals_v1` | Poisson | — |
| `nhl_assists_v1` | Poisson | +4.3% MAE |
| `nhl_saves_v1` | Poisson (goalies only) | — |

---

## Data

| Sport | Player-games | Players | Derived features |
|-------|-------------|---------|-----------------|
| MLB   | 221,083 | 2,169 | 137 |
| NBA   | 36,225  | 653   | 120 |
| WNBA  | 1,049   | 207   | 135 |
| NHL   | 440     | 108   | 114 |

Hourly PrizePicks scraper covers 40+ stat types across all four sports.

---

## Feature engineering

### MLB (137 features)
- Rolling averages (last 5/10/20 games + season) for all batting and pitching stats
- **Opposing pitcher quality** — ERA, K/9, H/9, BB/9, HR/9 over last 5 and 10 starts
- **Batter vs pitcher history** — career and recent results for the specific matchup
- **Platoon splits** — handedness advantage/disadvantage (left/right batter vs pitcher)
- **Lineup context** — opposing lineup HR rate, K rate, overall quality
- **Park factor** — per-ballpark HR and run-scoring adjustment
- Advanced batting metrics — BABIP, ISO, SLG, K%, BB%, hard contact rate
- Days rest, games played this season

### NBA (120 features)
- Rolling averages (last 5/10/20 games + season) for all box score stats
- **Basketball IQ** — usage rate, floor spacing score, foul drawing rate, paint scoring %, AST/PTS ratio, pts per FGA
- **Play type distribution** — isolation %, P&R ball handler %, spot-up % (from Second Spectrum)
- **Opposing team defense** — points/rebounds/assists allowed per position, pace-adjusted
- **Teammate absence** — expected usage bump when high-minute teammates are out
- **Home/away and back-to-back splits**
- **Playoff context** — series game number, series averages, is-playoff flag
- Market over probability (from Fanduel/DraftKings), team momentum, game total

### WNBA (135 features)
Full NBA feature set adapted for WNBA: rolling stats, basketball IQ, opponent positional defense, career vs opponent, team close-game rate.

### NHL (114 features)
- Rolling averages for goals, assists, points, shots, hits, blocked shots, powerplay stats
- **Goalie features** — save percentage trends, shots against, workload, GAA
- Powerplay goal/point rates, penalty minutes, faceoff rates
- Days rest, games played this season

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│  Data Sources                                    │
│  MLB Stats API · NBA API · ESPN · PrizePicks     │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│  Ingest Layer                                    │
│  Schedules · Box scores · Injuries · Prop lines  │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│  Feature Layer                                   │
│  Rolling stats · Matchup quality · Advanced IQ   │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│  Model Layer (11 LightGBM models)                │
│  Poisson regression · Binary classification      │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│  Picks Layer                                     │
│  Edge scoring · Line matching · Dedup · Logging  │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│  Output                                          │
│  PostgreSQL picks table · Dashboard · Discord    │
└─────────────────────────────────────────────────┘
```

### Deployment
- **Railway** — PostgreSQL + Streamlit app, auto-deploys on every push to `main`
- Dashboard: [prop-edge-production-1b02.up.railway.app](https://prop-edge-production-1b02.up.railway.app)
- MacBook cron runs `daily.sh` at 7am local time, writes to the Railway database
- Railway app reads and serves; no compute runs on Railway

---

## Dashboard

PrizePicks-style pick cards with:
- Player photo + team logo
- Line value, direction, model confidence
- **Line movement indicator** — shows if the line moved in the pick direction (sharp money signal)
- Form dots — last 5 game hit/miss vs the line
- Kelly sizing recommendation
- Combo stat cards (PRA, P+R, P+A, R+A)

Performance tab includes:
- Win rate over time vs 57.7% breakeven
- Edge bucket analysis (does higher edge = higher win rate?)
- Model calibration chart
- Win rate by stat type and direction

---

## Quality controls

- **Lookahead discipline** — every rolling feature uses `shift(1)` before aggregating. Train and inference paths share identical code.
- **Time-based train/test splits** — train before 2026-01-01, test on 2026+ data. No random splits.
- **Bench suppression** — players averaging < 12 min (last 10 games) are filtered out regardless of edge. High-variance bench players also suppressed.
- **Minimum line floors** — OVER 2.5 pts, OVER 2.5 reb, etc. filtered out as noise picks.
- **Starter confirmation** — pitcher picks voided if the starter is scratched before first pitch.
- **Two-way player settlement** — pitcher strikeout picks voided when the player didn't pitch (e.g. Ohtani as DH).
- **Self-healing game ID resolution** — mismatched placeholder game IDs auto-resolved at settlement.
- **Player deduplication** — same player/stat/direction/line can only be logged once per day.

---

## Quickstart

Prerequisites: Python 3.13+, PostgreSQL 16.

```bash
git clone https://github.com/aayushdave1003/prop-edge.git
cd prop-edge
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
createdb props
psql props < sql/schema.sql
```

Backfill and train:

```bash
python scripts/backfill_mlb.py
python scripts/backfill_nba.py
python -m props.features.mlb_rolling
python -m props.features.mlb_opposing_pitcher
python -m props.features.mlb_advanced_stats
python -m props.features.nba_rolling
python -m props.features.nba_basketball_iq
python -m props.models.strikeouts_v1
python -m props.models.hits_v1
python -m props.models.nba_points_v1
# ... (see props/models/ for all model scripts)
```

Daily ritual (automated via cron):

```bash
bash scripts/daily.sh
```

---

## Roadmap

- **FIFA World Cup 2026** (starts June 11) — shots on goal, goals, assists across 64 matches
- **NFL 2026 season** (starts September) — passing yards, rushing yards, receiving yards, TDs; highest-volume sport on PrizePicks
- **MLB park factors table** — per-ballpark HR adjustment to improve home_runs model
- **Post-Finals NBA rebounds** — retrain with Finals-intensity data once series ends
- **WNBA monthly retrains** — models improve significantly as 2026 season fills in

---

## Tech stack

Python 3.13, PostgreSQL 16, LightGBM, scikit-learn, pandas, scipy, SQLAlchemy, Streamlit, structlog, tenacity, nba_api, curl_cffi, pydantic.

---

## A note on intent

This project paper-tracks picks against publicly visible PrizePicks lines. It does not place real bets, does not interact with any accounts, and does not offer advice. It exists as a research artifact demonstrating end-to-end ML methodology on sports data.
