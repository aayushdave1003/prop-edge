# Railway build for the Streamlit dashboard.
#
# Why Docker instead of Nixpacks: Nixpacks builds Python against Nix's own
# dynamic linker, which does NOT search /usr/lib/x86_64-linux-gnu — so the
# apt-installed libgomp1 was present but invisible to LightGBM ("libgomp.so.1:
# cannot open shared object file"). A Debian base uses the standard glibc
# linker, so an apt-installed libgomp1 resolves normally.
FROM python:3.13-slim-bookworm

# libgomp1 provides libgomp.so.1, required by LightGBM at import time.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first for layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code + models (the dashboard imports `props` via its own sys.path insert).
COPY . .

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8501

# Liveness for THIS (Streamlit dashboard) service. NOTE: /api/health is the FastAPI
# BOARD service (Dockerfile.board), not this one — this container runs Streamlit,
# whose own health endpoint is /_stcore/health. The /api/health healthcheck lives
# in Dockerfile.board + railway.json. curl is installed above.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS "http://localhost:${PORT:-8501}/_stcore/health" || exit 1

# Railway injects $PORT.
CMD ["sh", "-c", "streamlit run ui/dashboard.py --server.port ${PORT:-8501} --server.address 0.0.0.0 --server.headless true"]
