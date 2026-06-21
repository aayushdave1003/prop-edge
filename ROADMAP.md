# prop-edge — Project Roadmap

Priorities: **P0** blocking/reliability · **P1** core value · **P2** quality/scale · **P3** future.
Status: ☐ todo · ◧ in progress.

**Open work only** — finished items auto-archive to [CHANGELOG.md](CHANGELOG.md) on commit
(via `scripts/clean_roadmap.py` + the pre-commit hook). The autonomous build is done; this is
what's left to build, by category.

---

## 1. New data → accuracy (more signal into the models)
- ☐ **P3** **Vegas game/team totals as a model feature — BLOCKED on data, not buildable now.** Assessed: **0 games store any historical implied-team-total / game total** (`games.context` holds only model outputs). MLB fetches no game-total source (only player-prop odds → `market_odds`); NBA fetches ESPN totals *live* for the winner model but never persists them, and the NBA season just ended. No historical column to train on. To enable: (1) persist `market_total`/`implied_team_total` per game going forward (MLB needs a new game-odds fetch), (2) accrue a season, (3) assess vs prop residuals + build. Months out; low priority (MLB models are data-saturated).
- ☐ **P3** **Pitcher velocity & pitch-mix trends** — declining velo flags fatigue/injury before results do; arsenal shifts move strikeout rates.
- ☐ **P3** **Times-through-order penalty** — a pitcher's 3rd time through the lineup spikes hits/runs allowed; a strong K/ER signal.
- ☐ **P3** **Bullpen rest / availability** — a gassed pen changes late-game run environment (totals, RBI).
- ☐ **P3** **Team defense (OAA / DRS)** — a strong defense suppresses BABIP → fewer hits allowed than the arm alone implies.
- ☐ **P3** **NBA referee tendencies** — crews differ on foul rates → pace + FT-dependent props (points).
- ☐ **P3** **NBA usage redistribution when a star sits** — extend teammate-absence beyond minutes to who absorbs the shots/assists.
- ☐ **P3** **Travel / rest / time-zone fatigue** — extend the NBA back-to-back signal across sports (road trips, altitude, get-away games).
- ☐ **P3** **Late scratches / beat-writer news** — catch lineup changes the injury feed misses (cross-check vs confirmed lineups).
- ☐ **P3** **Multi-book consensus** — average no-vig across more sharp books than DK/FD for a tighter "true" line.

## 2. Model / analytics
- ☐ **P3** **Retrain prod models on the full history for robustness (optional).** Prod hits was fit on ~2.8k rows of one 6-week 2024 window; the data now supports 135k continuous rows. MAE is neutral, but a model trained on 4 seasons is less fragile to distribution shift. Only worth it if it clears the (now in-domain) A/B gate — otherwise leave prod.
- ☐ **P3** **Model ensembling / stacking** — blend model versions (or a 2nd algorithm) per stat where it reduces MAE.
- ☐ **P3** **Playoff vs regular-season model split** — different distributions; a playoff-aware model (or feature) instead of suppressing playoff stats.

## 4. Ops / automation & data integrity
- ☐ **P3** **Residential proxy for PrizePicks** — provision `PRIZEPICKS_PROXY` so the scrape runs fully on GitHub Actions and retire the Mac-cron dependency (it goes stale when the laptop sleeps).
- ☐ **P3** **PrizePicks placeholders — rechecked: mostly expected, small genuine miss, benign.** The backfill did NOT change the rate — it was never a coverage problem (real games exist for those dates). The "~274k lines/14d" alarm was snapshot-inflated (~10×; actual ~2.4k distinct MLB props/day). Breakdown: **WNBA/NHL placeholders ≈95% untracked players** — international/exhibition events PrizePicks lists that aren't real WNBA/NHL games (correctly unresolvable). **MLB**: of placeholder players on a sample day, ~65% had no real game that day (off-day/cross-date/combo — correct), ~35% did play (genuine `resolve_game_id` miss, most likely **doubleheaders**, which it deliberately keeps as placeholder when >1 exact-date match). Only **1 pick ever** landed on a placeholder → zero recommendation impact. If pursued: add time-of-day disambiguation for doubleheaders + filter PrizePicks' non-league/exhibition events. Low priority.

## 5. Expansion (data-gated — unlocks as games/coverage accrue)
- ☐ **P3** Extend the **model/market blend + soft-line finder to NHL/WNBA** — auto-tunes in once those have sharp-market coverage.
- ☐ **P3** **New prop markets** — ✅ shipped: MLB `strikeouts_batter` (+4.78% MAE) and `hits_runs_rbis` (the #1 PrizePicks market, +1.68%); the 8 NBA/WNBA combos `pts_rebs_asts`/`pts_rebs`/`pts_asts`/`rebs_asts` (direct summed-target Poisson models, +3.7%→+26.6% vs baseline, all calibrated). The MLB batter-K + HRR pair ships on raw Poisson (sparse high-confidence tail blocks calibration; auto-calibrates as it densifies); combos calibrated fine (higher means → dense tails). **Assessed and dropped** (all worse than their season-avg baseline — low-frequency, high-variance events that aren't modelable beyond the season mean): `runs` (−1.8%), `singles` (−0.9%), `walks` (−3.0%), `doubles` (−4.7%), `stolen_bases` (−9.4%). ✅ also shipped MLB pitcher `earned_runs_allowed` (+8% MAE) and `hits_allowed` (+18%) — these accumulate over ~25 batters/start so (unlike the batter low-frequency events) they carry real signal. **Net: the modelable new markets are built (12 total); the low-frequency batter events are confirmed not modelable. Lane effectively complete** — further markets would be marginal niche types.
- ☐ **P3** **New sports** — soccer / tennis / golf / UFC or CBB/CFB props (each needs its own ingest + models; biggest lift, biggest surface-area).
