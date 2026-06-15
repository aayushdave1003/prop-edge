# Deploying the prop-edge Discord slash-bot

The bot answers `/picks`, `/record`, `/player <name>` from Discord by querying the
prod DB read-only. It's a small FastAPI service (`props/bot/discord_interactions.py`)
that runs as a **second Railway service** off this repo. Everything code-side is
ready (`Dockerfile.bot`, `props/bot/requirements-bot.txt`, signature verification,
prod-verified handlers) — these steps are the account-specific bits only you can do.

## 1. Create the Discord application (~2 min)
1. https://discord.com/developers/applications → **New Application** → name it.
2. **General Information** → copy the **Public Key** → this is `DISCORD_PUBLIC_KEY`.
3. Copy the **Application ID** → `DISCORD_APP_ID`.
4. **Bot** (left nav) → **Reset Token** → copy → `DISCORD_BOT_TOKEN`.

## 2. Deploy the Railway service (~3 min)
1. Railway → your project → **New → GitHub Repo** → pick `prop-edge` (a second
   service alongside the dashboard).
2. Service **Settings → Build**: set **Dockerfile Path** = `Dockerfile.bot`.
3. Service **Variables**: set
   - `DATABASE_URL` = the same Railway Postgres URL the dashboard uses
     (`postgresql+psycopg://…`),
   - `DISCORD_PUBLIC_KEY` = from step 1.
   (Railway provides `PORT` automatically.)
4. **Settings → Networking → Generate Domain** → note the public URL, e.g.
   `https://prop-edge-bot-production.up.railway.app`.

## 3. Register the slash commands (one-time)
Run locally (or as a Railway one-off) with `DISCORD_APP_ID` + `DISCORD_BOT_TOKEN` set:
```
pip install requests
DISCORD_APP_ID=… DISCORD_BOT_TOKEN=… python -m props.bot.register_commands
```
Expect `HTTP 200`. This bulk-registers `/picks /record /player` (safe to re-run).

## 4. Point Discord at the service
Discord dev portal → your app → **General Information** → **Interactions Endpoint
URL** = `<your Railway bot URL>/interactions` → **Save**. Discord sends a signed
PING; the service answers PONG (it verifies the signature with `DISCORD_PUBLIC_KEY`),
and Discord accepts the URL. If it rejects, double-check the Public Key.

## 5. Add the bot to your server & test
**OAuth2 → URL Generator** → scopes `bot` + `applications.commands` → open the URL,
add it to your server. Then in any channel: `/record` should reply with the
recommended-tier W/L (prod-verified: e.g. *162–79 (67.2%) ✅*).

---

**Notes**
- Read-only: the bot only `SELECT`s from picks/players — it never logs picks or
  writes anything.
- Lean image: `Dockerfile.bot` installs only `requirements-bot.txt` (no lightgbm/
  pandas), so it builds fast and stays small.
- Health check: `GET /health` → `{"ok": true}` (point Railway's healthcheck here).
