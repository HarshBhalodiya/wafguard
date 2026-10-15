# WafGuard AI — API image (Phase 9). Build from the REPO ROOT:
#   docker build -f backend/Dockerfile -t wafguard-api .
# Paths in app/config.py resolve against the project root (/app), so the image
# mirrors the repo layout: /app/backend (code), /app/docs (copilot doc index),
# /app/data + /app/backend/models/artifacts (mounted volumes, not baked in).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# libgomp: XGBoost OpenMP runtime; libglib2.0-0: opencv-headless runtime dep.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend
COPY backend/requirements.txt ./requirements.txt
# CPU-only torch wheel first (the default index pulls a multi-GB CUDA build).
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch \
    && pip install -r requirements.txt

COPY backend/ /app/backend/
COPY docs/ /app/docs/

RUN useradd --create-home --uid 10001 wafguard \
    && mkdir -p /app/data/synthetic /app/data/secom /app/backend/models/artifacts /app/mlruns \
    && chown -R wafguard /app
USER wafguard

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health/live || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
