"""FastAPI app serving the prop-edge pick board (research / paper-tracking).

Run:  uvicorn props.api.server:app --reload --port 8000
  GET /api/health
  GET /api/leagues
  GET /api/picks?league=&stat=&stat=&direction=&recommended=
  GET /api/games?league=
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from props.api.repo import (
    fetch_picks, fetch_leagues, fetch_games, fetch_performance, fetch_soft_lines,
)

app = FastAPI(title="prop-edge API", version="2.0")

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
    league: str | None = Query(default=None),
    stat: list[str] | None = Query(default=None, description="repeatable internal stat_type"),
    direction: str | None = Query(default=None, description="over|under"),
    recommended: int = Query(default=0, description="1 = recommended only"),
) -> dict:
    return fetch_picks(
        league=league, stats=stat, direction=direction, recommended_only=bool(recommended)
    )


@app.get("/api/games")
def games(league: str | None = Query(default=None)) -> dict:
    return {"games": fetch_games(league=league)}


@app.get("/api/performance")
def performance() -> dict:
    return fetch_performance()


@app.get("/api/soft_lines")
def soft_lines(league: str | None = Query(default=None)) -> dict:
    return {"soft_lines": fetch_soft_lines(league=league)}


# Serve the built React board (web/dist) from the SAME service, so the SPA calls
# /api/* same-origin — one Railway service, one URL. Mounted LAST so it doesn't
# shadow the /api routes. Skipped if the build isn't present (e.g. running the
# API bare in local dev, where Vite serves the frontend on :5173 instead).
_DIST = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "web", "dist")
if os.path.isdir(_DIST):
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="board")
