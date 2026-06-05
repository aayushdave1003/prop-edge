"""Resilient, batched, incremental writer for ``player_games.derived``.

Every feature module used to UPDATE ``player_games.derived`` one row at a time
inside a single transaction that stayed open for the entire job. Against the
remote Railway DB (over the ``interchange.proxy.rlwy.net`` proxy) that meant a
~40-minute connection that the proxy would terminate mid-write — aborting the
whole daily pipeline before any picks were generated.

This writer fixes that:
  * **Batched** ``executemany`` (psycopg pipelines it) — ~100x fewer round-trips.
  * **Per-batch commit** — short transactions, so the connection is never held
    open for minutes and a transient blip loses at most one batch.
  * **Retry** on ``OperationalError`` with backoff for transient proxy drops.
  * **Incremental** — by default only rows for games in the last
    ``DEFAULT_RECENT_DAYS`` are written (rolling features for old games are
    stable once computed). Set ``DERIVED_BACKFILL_ALL=1`` to rewrite everything
    (one-time backfill, or after adding/altering a feature).

Two modes preserve the original chaining semantics:
  * ``mode="replace"`` — ``derived = feat`` (the ``*_rolling`` base modules).
  * ``mode="merge"``   — ``derived = derived || feat`` (additive modules that
    layer extra keys on top). This replaces the old read-modify-write loop, so
    the per-row SELECT is gone too.
"""
import json
import os
import time

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from props.utils.db import engine, session_scope
from props.utils.logging import log

DEFAULT_RECENT_DAYS = 21


def _guard_prod_backfill() -> None:
    """Refuse a full (DERIVED_BACKFILL_ALL) rewrite against a remote DB.

    A full backfill rewrites every row's derived JSONB and floods WAL — that is
    exactly what filled the Railway volume and crashed prod on 2026-06-05.
    Incremental daily writes are tiny and safe; a full backfill belongs on a
    local DB, or on prod only after confirming disk headroom (override via
    DERIVED_ALLOW_PROD_BACKFILL=1).
    """
    if not os.getenv("DERIVED_BACKFILL_ALL"):
        return
    if os.getenv("DERIVED_ALLOW_PROD_BACKFILL"):
        return
    host = (engine.url.host or "localhost").lower()
    if host not in ("localhost", "127.0.0.1", ""):
        raise RuntimeError(
            f"Refusing DERIVED_BACKFILL_ALL against remote DB '{host}': a full "
            "backfill filled the Railway disk and crashed prod. Run incrementally "
            "(unset the flag), or set DERIVED_ALLOW_PROD_BACKFILL=1 only after "
            "verifying the volume has headroom."
        )


def feat_dict(row, feature_cols) -> dict:
    """Coerce a feature-frame row to a JSON-safe {col: number} dict (NaN -> 0)."""
    out = {}
    for c in feature_cols:
        v = row[c]
        if pd.isna(v):
            out[c] = 0
        elif isinstance(v, (np.integer, int)):
            out[c] = int(v)
        else:
            out[c] = float(v)
    return out


def _recent_player_game_ids(days: int) -> set:
    sql = text("""
        SELECT pg.player_game_id
        FROM player_games pg
        JOIN games g USING (game_id)
        WHERE g.game_date >= CURRENT_DATE - :days
    """)
    with session_scope() as s:
        return {r[0] for r in s.execute(sql, {"days": days}).all()}


def write_derived(items, *, mode: str = "merge", label: str = "derived",
                  recent_days: int | None = DEFAULT_RECENT_DAYS,
                  batch_size: int = 500, max_retries: int = 4) -> int:
    """Write ``[(player_game_id, feat_dict), ...]`` to ``player_games.derived``.

    Returns the number of rows actually written. ``DERIVED_BACKFILL_ALL=1`` in
    the environment forces a full rewrite regardless of ``recent_days``.
    """
    _guard_prod_backfill()
    items = [(int(pid), feat) for pid, feat in items]
    candidates = len(items)

    if os.getenv("DERIVED_BACKFILL_ALL"):
        recent_days = None
    if recent_days is not None:
        keep = _recent_player_game_ids(recent_days)
        items = [(pid, feat) for pid, feat in items if pid in keep]

    if not items:
        log.info("write_derived_skip", label=label, candidates=candidates, written=0)
        return 0

    if mode == "replace":
        set_expr = "CAST(:f AS JSONB)"
    elif mode == "merge":
        set_expr = "COALESCE(derived, '{}'::jsonb) || CAST(:f AS JSONB)"
    else:
        raise ValueError(f"unknown mode: {mode!r}")

    stmt = text(f"UPDATE player_games SET derived = {set_expr}, updated_at = NOW() "
                f"WHERE player_game_id = :pid")

    written = 0
    for i in range(0, len(items), batch_size):
        params = [{"f": json.dumps(feat), "pid": pid}
                  for pid, feat in items[i:i + batch_size]]
        for attempt in range(max_retries):
            try:
                with session_scope() as s:
                    s.execute(stmt, params)        # executemany — pipelined
                written += len(params)
                break
            except OperationalError as e:
                if attempt == max_retries - 1:
                    raise
                wait = 2 ** attempt
                log.warning("write_derived_retry", label=label, attempt=attempt + 1,
                            wait=wait, error=str(e)[:100])
                time.sleep(wait)
        if (i // batch_size) % 10 == 0:
            log.info("write_derived_progress", label=label, done=written, total=len(items))

    log.info("write_derived_complete", label=label, written=written,
             candidates=candidates, mode=mode,
             incremental=(recent_days is not None))
    return written
