# prop-edge — Pick Board UI

React + Vite + TypeScript + Tailwind board that renders model picks from the
prop-edge backend. The frontend is "dumb": it renders whatever `/api` returns and
never computes edge, recommendation, or labels client-side.

## Run (dev)

Two processes — the FastAPI API and the Vite dev server.

```bash
# 1) API (from repo root) — point DATABASE_URL at the DB with today's picks
DATABASE_URL="$RAILWAY_DATABASE_URL" uvicorn props.api.server:app --reload --port 8000

# 2) frontend (from web/)
npm install
npm run dev          # http://localhost:5173  (proxies /api -> :8000)
```

No backend handy? Run against the bundled fixtures:

```bash
VITE_USE_MOCK=1 npm run dev
```

## Build

```bash
npm run build        # tsc typecheck + vite production build -> dist/
npm run preview      # serve the built bundle
```

## API contract

`GET /api/picks?league=nba&stat=points`

```json
{
  "picks": [
    {
      "id": "string",
      "league": "nba",
      "player": { "name": "string", "team": "LAL", "headshot_url": "string|null" },
      "matchup": "LAL @ DEN",
      "start_time": "2026-06-25T19:30:00Z",
      "stat_type": "Points",
      "stat_key": "points",
      "pp_line": 24.5,
      "model_projection": 26.8,
      "edge_pct": 9.4,
      "recommendation": "more",
      "confidence": "high"
    }
  ]
}
```

`GET /api/leagues` powers the filter rows (leagues with picks today + each
league's available stat types). `league`/`stat` query params are optional.

- `edge_pct` = signed projection-vs-line gap, `(projection - line) / line * 100`.
- `recommendation` = the model-favored side (`more`/`less`), from the pick's direction.
- `confidence` = `low|med|high`, tiered off the model probability vs the
  per-category recommend cutoff.

## Palette

Brand is a single CSS variable (`--brand` in `src/index.css`), default electric
cyan `#22D3EE`. To switch to the indigo/violet alt palette, set `--brand` to
`#6366F1` and `--brand-glow` accordingly — nothing else changes. Brand-cyan =
identity/navigation/selection; edge-green `#34D399` = a positive model edge. The
two never swap jobs.
