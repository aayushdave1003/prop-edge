import logging
import uuid

import structlog

from props.utils.config import settings


def configure_logging(run_id: str | None = None):
    """Configure structlog and bind a per-run id onto every subsequent log line,
    so the lines from one pipeline run / process are correlatable in the output."""
    logging.basicConfig(level=settings.log_level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,   # carry the bound run_id
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ]
    )
    bind_run_id(run_id)


def bind_run_id(run_id: str | None = None) -> str:
    """Bind a short run id to the logging context (generated if not given)."""
    rid = run_id or uuid.uuid4().hex[:8]
    structlog.contextvars.bind_contextvars(run_id=rid)
    return rid


log = structlog.get_logger()
