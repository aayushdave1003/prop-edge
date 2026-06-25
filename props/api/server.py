"""FastAPI app serving the prop-edge pick board.

Run:  uvicorn props.api.server:app --reload --port 8000
Endpoints:
  GET /api/health            -> liveness
  GET /api/leagues           -> leagues w/ picks today + their stat types
  GET /api/picks?league&stat -> today's picks (contract in web/ README)
"""
from __future__ import annotations

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from props.api.repo import fetch_picks, fetch_leagues

app = FastAPI(title="prop-edge API", version="1.0")

# The board is a static SPA on a different origin in dev (Vite :5173) and may be
# served from anywhere in prod; this API is public, read-only model output.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/leagues")
def leagues() -> dict:
    return {"leagues": fetch_leagues()}


@app.get("/api/picks")
def picks(
    league: str | None = Query(default=None, description="sport_code, e.g. nba"),
    stat: str | None = Query(default=None, description="internal stat_type, e.g. points"),
) -> dict:
    return {"picks": fetch_picks(league=league, stat=stat)}
