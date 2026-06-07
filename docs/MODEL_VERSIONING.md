# Model versioning & retrain cadence

Lightweight policy — no MLOps platform, just clear rules so models stay honest
and a bad retrain can be rolled back in one commit.

## Naming

Each model is `{sport}_{stat}_v{N}` (e.g. `nba_points_v1`, `hits_v1`). Artifacts
live in `models/`:

| File | Purpose |
|------|---------|
| `{name}_v{N}.txt` | LightGBM booster |
| `{name}_v{N}_meta.json` | feature list, training window, params |
| `{name}_v{N}_calibrator.pkl` | isotonic calibrator (where it passed the degenerate-map guard) |

The `registry.py` `MODELS` list is the single source of truth for which version
is **live**. `model_versions` (DB table: `name`, `stat_type`, `sport_code`,
`trained_at`, `notes`) records each version picks were logged against.

## When to bump `v{N}`

Bump the version (don't overwrite a live artifact) when any of these change:

- New/changed features, params, or training window.
- A retrain on materially more data.
- A calibrator added/replaced.

Overwriting `_v1` in place is forbidden — it silently changes history and breaks
the ability to roll back. New training ⇒ new `_v{N+1}` ⇒ update `registry.py`.

## Retrain cadence

Driven by evidence, not the calendar. Re-run the report and act on it:

```
python -m props.models.holdout_report      # per-category win rate + calibration + drift
python -m props.models.category_cutoffs    # refresh the live recommended cutoffs
```

Retrain a model when **any** trigger fires:

1. **Calibration drift** — its prob buckets are off by a lot (weighted MAE
   > ~0.10, or a populated bucket where realized win rate is >0.10 below
   predicted). Usually fixable by re-fitting the calibrator alone (cheaper than
   a full retrain) — see `props/models/calibrate_models.py`.
2. **Performance decay** — `recent_drift` shows a sport/stat slipping below the
   57.7% breakeven over the last 21 days after previously clearing it.
3. **Data milestone** — enough new settled games to meaningfully expand the
   training set (e.g. NHL/WNBA crossing the thresholds in `ROADMAP.md §2`).

Floor cadence: review the report at least monthly even if nothing alarms.

## Guardrail (no retrain needed)

The per-category cutoffs (`category_cutoffs.json`, recomputed live every 6h) are
the safety net between retrains: they tune on **realized** win rate, so a model
that drifts overconfident is automatically de-recommended (or suppressed)
without touching the model. Retraining fixes the model; cutoffs protect the
slate in the meantime.

## Rollback

Because artifacts are versioned and never overwritten:

1. Point `registry.py` back to the previous `_v{N-1}` paths.
2. Commit. (The old `.txt`/`.json`/`.pkl` are still in `models/`.)
3. Redeploy. Picks immediately log against the restored version.

No DB surgery required — `ensure_model_version` re-registers the name on next
run, and existing picks keep their original `model_version_id`.
