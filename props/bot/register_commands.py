"""Register the prop-edge slash commands with Discord (run once after setup).

Needs DISCORD_APP_ID + DISCORD_BOT_TOKEN. Bulk-overwrites the app's global
commands, so it's safe to re-run.

Run:  python -m props.bot.register_commands
"""
import requests

from props.utils.config import settings

COMMANDS = [
    {"name": "picks", "type": 1,
     "description": "Today's recommended prop-edge slate"},
    {"name": "record", "type": 1,
     "description": "Overall + recommended-tier record vs breakeven"},
    {"name": "player", "type": 1,
     "description": "A player's settled prop-edge record",
     "options": [{"name": "name", "type": 3, "required": True,
                  "description": "Player full name (e.g. Aaron Judge)"}]},
]


def main():
    app_id, token = settings.discord_app_id, settings.discord_bot_token
    if not app_id or not token:
        raise SystemExit("Set DISCORD_APP_ID and DISCORD_BOT_TOKEN first.")
    r = requests.put(
        f"https://discord.com/api/v10/applications/{app_id}/commands",
        headers={"Authorization": f"Bot {token}"}, json=COMMANDS, timeout=20)
    print(f"register_commands: HTTP {r.status_code}")
    print(r.text[:400])
    r.raise_for_status()


if __name__ == "__main__":
    main()
