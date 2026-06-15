import re
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from props.utils.config import settings

# TCP keepalives stop the Railway proxy from silently dropping a connection
# during long writes; pool_recycle retires connections before the proxy's idle
# limit. pool_pre_ping catches any that died while checked back in.
engine = create_engine(
    settings.database_url,
    future=True,
    pool_pre_ping=True,
    pool_recycle=1800,
    connect_args={
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
    },
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

@contextmanager
def session_scope():
    """Use this for every database interaction."""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def _host(url: str) -> str:
    m = re.search(r"@([^/?]+)", url or "")          # host after credentials
    if m:
        return m.group(1)
    m = re.search(r"://([^/?]+)", url or "")          # no-creds form (localhost/props)
    return m.group(1) if m else ""


def db_target() -> tuple[str, bool]:
    """(active DB host, is_prod) — compares DATABASE_URL to the configured prod
    (RAILWAY_DATABASE_URL). Used to guard against the local-vs-prod mix-up that
    once faked a 10-day outage (local .env DATABASE_URL=localhost/props is stale)."""
    host = _host(settings.database_url)
    prod_host = _host(settings.railway_database_url)
    return (host or "?", bool(prod_host) and host == prod_host)


def db_banner() -> str:
    """One-line banner naming which DB a command is hitting (prod vs local)."""
    host, is_prod = db_target()
    if is_prod:
        return f"🛢️  DB: {host} (PROD)"
    return (f"⚠️  DB: {host} (LOCAL / non-prod — for prod run with "
            "DATABASE_URL=$RAILWAY_DATABASE_URL)")
