# syntax=docker/dockerfile:1
# ── Stage 1: build wheel ──────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

RUN pip install --no-cache-dir "build==1.2.*"

COPY pyproject.toml README.md ./
COPY src/ src/

RUN python -m build --wheel --outdir /dist


# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    MANGOMAS_ENV=prod \
    MANGOMAS_LOG__FORMAT=json

# Non-root user — no home dir, no interactive shell
RUN useradd --no-create-home --shell /bin/false mangomas

WORKDIR /app

COPY --from=builder /dist/*.whl /tmp/wheels/

RUN pip install --no-cache-dir /tmp/wheels/*.whl \
    && rm -rf /tmp/wheels

# Create writable data dir for default SQLite path (./data/mangomas.db) and the
# compose-mounted /data volume.  Both are owned by the non-root user.
RUN mkdir -p /app/data /data \
    && chown -R mangomas:mangomas /app /data

USER mangomas

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\", \"8000\")}/healthz')" || exit 1

CMD ["sh", "-c", "exec python -m uvicorn mangomas.api.app:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}"]
