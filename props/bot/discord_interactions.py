"""Discord slash-command bot — query prop-edge on demand from Discord.

The rest of prop-edge is serverless (GHA cron + Streamlit), but Discord slash
commands need a public endpoint Discord can POST to per command, so this is a
small FastAPI service you deploy SEPARATELY (a second Railway service off the
same repo). Commands:

  /picks            today's recommended slate
  /record           overall + recommended-tier W/L vs the 57.7% breakeven
  /player <name>    one player's settled record

Deploy:
  1. Discord dev portal → New Application → copy Public Key, Bot Token, App ID.
  2. Set DISCORD_PUBLIC_KEY / DISCORD_BOT_TOKEN / DISCORD_APP_ID (+ DATABASE_URL).
  3. `python -m props.bot.register_commands`   (one-time, registers the commands)
  4. Run the service:
        pip install -r requirements.txt -r requirements-bot.txt
        uvicorn props.bot.discord_interactions:app --host 0.0.0.0 --port $PORT
  5. Set the app's "Interactions Endpoint URL" to the deployed /interactions URL.

Read-only — it only queries the DB; it never logs picks or places anything.
"""
from sqlalchemy import text

from props.utils.config import settings
from props.utils.db import engine
from props.models.category_cutoffs import rec_cutoff

try:
    from fastapi import FastAPI, Request, HTTPException
    from nacl.signing import VerifyKey
    from nacl.exceptions import BadSignatureError
except Exception:                       # deps only present in the bot service
    FastAPI = None

PONG = {"type": 1}
CHANNEL_MSG = 4                          # interaction response: channel message


def _verify(signature: str, timestamp: str, body: bytes) -> bool:
    key = settings.discord_public_key
    if not key:
        return False
    try:
        VerifyKey(bytes.fromhex(key)).verify(timestamp.encode() + body,
                                             bytes.fromhex(signature))
        return True
    except (BadSignatureError, ValueError):
        return False


def _msg(content: str) -> dict:
    return {"type": CHANNEL_MSG, "data": {"content": content}}


# ── command handlers (read-only DB queries) ──────────────────────────────────
def cmd_picks() -> str:
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT g.sport_code, p.full_name, pk.stat_type, pk.direction,
                   pl.line_value::float AS line, pk.model_prob::float AS mp
            FROM picks pk JOIN players p USING (player_id)
            JOIN games g USING (game_id)
            JOIN prop_lines pl ON pl.line_id = pk.line_id
            WHERE (pk.picked_at AT TIME ZONE 'America/Los_Angeles')::date
                  = (NOW() AT TIME ZONE 'America/Los_Angeles')::date
              AND (pk.leg_result IS NULL OR pk.leg_result <> 'void')
            ORDER BY pk.model_prob DESC
        """)).all()
    rec = [r for r in rows if r.mp >= rec_cutoff(r.sport_code, r.stat_type,
                                                 direction=r.direction)]
    if not rec:
        return "No recommended picks today yet — the slate posts each morning."
    lines = [f"• **{r.full_name}** {r.direction.upper()} {r.line:g} {r.stat_type} "
             f"({r.mp*100:.0f}%)" for r in rec[:12]]
    return f"⚡ **Recommended today ({len(rec)})**\n" + "\n".join(lines)


def cmd_record() -> str:
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT sport_code, stat_type, direction, model_prob::float AS mp,
                   leg_result FROM picks
            WHERE leg_result IN ('win','loss') AND model_prob IS NOT NULL
        """)).all()
    aw = sum(r.leg_result == "win" for r in rows)
    rec = [r for r in rows if r.mp >= rec_cutoff(r.sport_code, r.stat_type,
                                                 direction=r.direction)]
    rw = sum(r.leg_result == "win" for r in rec)
    rn, an = len(rec), len(rows)
    rwr = rw / rn * 100 if rn else 0
    return (f"📊 **prop-edge record**\nRecommended tier: **{rw}–{rn-rw} ({rwr:.1f}%)** "
            f"vs 57.7% breakeven {'✅' if rwr >= 57.7 else '🔻'}\n"
            f"All picks: {aw}–{an-aw} ({aw/an*100:.0f}%) · {an} settled")


def cmd_player(name: str) -> str:
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT pk.stat_type, pk.direction, pk.leg_result
            FROM picks pk JOIN players p USING (player_id)
            WHERE lower(p.full_name) = lower(:nm) AND pk.leg_result IN ('win','loss')
        """), {"nm": name}).all()
    if not rows:
        return f"No settled picks found for **{name}**."
    w = sum(r.leg_result == "win" for r in rows)
    return f"🔎 **{name}** — {w}–{len(rows)-w} ({w/len(rows)*100:.0f}%) over {len(rows)} settled picks."


def _handle(data: dict) -> str:
    name = data.get("name")
    if name == "picks":
        return cmd_picks()
    if name == "record":
        return cmd_record()
    if name == "player":
        opts = {o["name"]: o["value"] for o in data.get("options", [])}
        return cmd_player(opts.get("name", ""))
    return "Unknown command."


# ── FastAPI app (only constructed when the deps are installed) ────────────────
if FastAPI is not None:
    app = FastAPI(title="prop-edge bot")

    @app.post("/interactions")
    async def interactions(request: Request):
        body = await request.body()
        sig = request.headers.get("X-Signature-Ed25519", "")
        ts = request.headers.get("X-Signature-Timestamp", "")
        if not _verify(sig, ts, body):
            raise HTTPException(status_code=401, detail="bad signature")
        payload = await request.json()
        if payload.get("type") == 1:           # PING (endpoint verification)
            return PONG
        if payload.get("type") == 2:           # APPLICATION_COMMAND
            try:
                return _msg(_handle(payload.get("data", {})))
            except Exception as e:
                return _msg(f"⚠️ error: {str(e)[:120]}")
        return _msg("unsupported interaction")

    @app.get("/health")
    async def health():
        return {"ok": True}
