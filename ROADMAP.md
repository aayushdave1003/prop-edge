# prop-edge — Project Roadmap

Priorities: **P0** blocking/reliability · **P1** core value · **P2** quality/scale · **P3** future.
Status: ☐ todo · ◧ in progress.

**Open work only** — finished items auto-archive to [CHANGELOG.md](CHANGELOG.md) on commit
(via `scripts/clean_roadmap.py` + the pre-commit hook). The autonomous build is done; this is
what's left to build, by category.

---

## 1. New data → accuracy (more signal into the models)
- ☐ **P2** **Vegas game/team totals as a model feature** — live odds flow now; high implied team total = more offense. Feed it into the MLB/NBA models.
- ☐ **P2** **Statcast batted-ball quality** — exit velocity / barrel% / xwOBA capture true hitter form better than raw results (luck-adjusted).
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
- ☐ **P3** **CLV as a training signal** — train toward beating the closing line, not just the realized stat (rewards finding soft lines).
- ☐ **P3** **Monte-Carlo parlay simulation** — simulate the full joint distribution of a slate (with the correlation matrix) for true parlay EV + variance, beyond the pairwise approximation.
- ☐ **P3** **Slate-level Kelly / portfolio sizing** — size the whole slate jointly (correlation-aware) instead of per-leg, to optimize bankroll growth vs variance.
- ☐ **P3** **Playoff vs regular-season model split** — different distributions; a playoff-aware model (or feature) instead of suppressing playoff stats.
- ☐ **P3** **Hierarchical / player random-effects** — partial-pooling for low-sample players (rookies, call-ups) instead of league priors.

## 4. Ops / automation & data integrity
- ◧ **P2** **Player-identity reconciliation** — fuzzy box-score name matching mis-attributes games (Jared McCain picked up **45 phantom OKC games** he never played). ✅ Audit shipped: `data_audit` flags NBA/WNBA players spanning >2 teams (**23 candidates**) + **234 combo-name junk rows**. REMAINING (deferred — risky): re-key NBA/WNBA players by authoritative ESPN athlete ids + un-merge the mis-attributed games. Touches FK-referenced picks across thousands of rows, so it needs a reviewed migration, not a blind auto-merge.
- ☐ **P3** **Residential proxy for PrizePicks** — provision `PRIZEPICKS_PROXY` so the scrape runs fully on GitHub Actions and retire the Mac-cron dependency (it goes stale when the laptop sleeps).

## 5. Expansion (data-gated — unlocks as games/coverage accrue)
- ☐ **P1** Deepen **NHL** (~23 games) and **WNBA** (~116) history so prop models get signal and winner models become trainable.
- ☐ **P3** Train **NHL/WNBA winner models** once data is sufficient (WNBA first — basketball-generic, revisit ~150+ games).
- ☐ **P3** Extend the **model/market blend + soft-line finder to NHL/WNBA** — auto-tunes in once those have sharp-market coverage.
- ☐ **P3** **New prop markets** — more stat types per sport (e.g. NBA turnovers/blocks-steals depth, MLB stolen bases) as their settled history grows.
- ☐ **P3** **New sports** — soccer / tennis / golf / UFC or CBB/CFB props (each needs its own ingest + models; biggest lift, biggest surface-area).
