# Deploying the pick board (prop-edge-board)

The board is a **second Railway service** off this same repo: one container that
runs the FastAPI API (`/api/*`) **and** serves the built React SPA (`web/dist`),
so the frontend calls the API same-origin. It reads the same Railway Postgres the
rest of prop-edge writes.

## Option A — Railway dashboard (GitHub-connected, auto-deploys on push)

1. **New Service** → *Deploy from GitHub repo* → `aayushdave1003/prop-edge`.
2. **Settings → Build**: set **Dockerfile Path** = `Dockerfile.board`
   (or add the service variable `RAILWAY_DOCKERFILE_PATH=Dockerfile.board`).
3. **Variables**: set `DATABASE_URL` (and `RAILWAY_DATABASE_URL`) to the Postgres
   service — use the reference `${{Postgres.DATABASE_URL}}` for fast internal
   networking. `props.utils.db` normalizes `postgresql://` → `+psycopg`.
4. **Networking → Generate Domain** to get a public URL. Railway injects `$PORT`.

That's it — the SPA loads at the domain root, `/api/picks` etc. answer under it,
and every push to `main` redeploys.

## Option B — Railway CLI

```bash
railway add \
  --service prop-edge-board \
  --repo aayushdave1003/prop-edge \
  --variables "RAILWAY_DOCKERFILE_PATH=Dockerfile.board" \
  --variables 'DATABASE_URL=${{Postgres.DATABASE_URL}}' \
  --variables 'RAILWAY_DATABASE_URL=${{Postgres.DATABASE_URL}}'
# then, in the dashboard, Networking → Generate Domain (or `railway domain`).
```

## Notes

- **Deps:** the image installs the full `requirements.txt` for import safety (the
  API pulls in the model-helper modules for calibration/cutoffs/slate-Kelly). It
  runs no LightGBM inference — it reads predictions from the DB — so this can be
  slimmed to a lean `requirements-api.txt` later if build time matters.
- **Read-only:** the board only reads. No pipeline/write paths run in this service.
- **BOARD_DATE:** unset in prod (defaults to today). Set it on the service only to
  demo a specific past slate.
