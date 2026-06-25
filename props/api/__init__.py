"""Thin web API that serves model picks to the React pick-board UI (web/).

Separate from `props.bot.discord_interactions` (the Discord-interactions service):
this is the public, read-only JSON API the browser frontend calls. It reuses the
shared DB engine and mirrors the dashboard's pick-board query so the numbers match
exactly — it never recomputes model output.
"""
