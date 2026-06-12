# ==============================================================================
# openschichtplaner5-api — produktionsreifes Multi-Stage-Image
#
# Builder installiert das Paket + libopenschichtplaner5 in ein venv; das
# Runtime-Image (slim, non-root, ohne Build-Tools) übernimmt nur das venv.
#
# Build-Arg LIB_SOURCE: pip-Requirement für die Library. Default ist der
# PyPI-Pin (reproduzierbare Builds). Override-Beispiele:
#   --build-arg LIB_SOURCE=git+https://github.com/mschabhuettl/libopenschichtplaner5.git@main   (Entwicklungs-Stand)
#   --build-arg LIB_SOURCE="libopenschichtplaner5[postgres]==1.9.0"   (künftige Version)
#
# ENV-Defaults (Runtime):
#   SP5_DB_PATH=/app/data        Verzeichnis der 5*.DBF-Dateien (Volume mounten!)
#   SP5_BACKEND_DIR=/app/backend Ressourcen-Root: data/ (JSON-Stores),
#                                api/data + api/uploads, backups/ (Auto-Migrate)
#   Weitere (per -e/.env setzen): SECRET_KEY (Pflicht für Produktion!),
#   ALLOWED_ORIGINS, SP5_DEV_MODE, TOKEN_EXPIRE_HOURS, RATE_LIMIT_*,
#   DB_BACKEND/DATABASE_URL (PostgreSQL-Backend), SP5_FRONTEND_DIST (SPA).
#
# Betrieb: docker compose up  (siehe docker-compose.yml — DBF-Volume + .env)
# ==============================================================================

# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

# git für die git+https-Installation der Library
RUN apt-get update && apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

ARG LIB_SOURCE="libopenschichtplaner5[postgres]==1.9.0"

WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY sp5api/ sp5api/

# Library zuerst aus LIB_SOURCE (PyPI-Pin), psycopg2-binary für das
# [postgres]-Extra; danach erfüllt die installierte Version die
# libopenschichtplaner5[postgres]>=…-Abhängigkeit des API-Pakets.
RUN pip install --no-cache-dir "${LIB_SOURCE}" psycopg2-binary && \
    pip install --no-cache-dir .

# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim

# curl nur für den Docker-HEALTHCHECK
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1001 sp5 && \
    useradd --uid 1001 --gid sp5 --shell /usr/sbin/nologin --create-home sp5

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    SP5_DB_PATH=/app/data \
    SP5_BACKEND_DIR=/app/backend

# DBF-Mount-Punkt + mutabler Laufzeit-State (JSON-Stores, Uploads, Backups) —
# sp5-owned, damit Named Volumes beim ersten Start beschreibbar geseedet werden.
# Nur die im Repo getrackten Seeds werden kopiert, nie lokaler Laufzeit-State.
COPY --chown=sp5:sp5 data/skills.json data/wishes.json data/notification_settings.json /app/backend/data/
COPY --chown=sp5:sp5 api/data/skills.json /app/backend/api/data/
RUN mkdir -p /app/data /app/backend/api/uploads /app/backend/backups && \
    chown -R sp5:sp5 /app/data /app/backend

WORKDIR /app
VOLUME ["/app/data"]
USER sp5

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=15s \
  CMD curl -f http://localhost:8000/api/health || exit 1
CMD ["python", "-m", "uvicorn", "sp5api.main:app", "--host", "0.0.0.0", "--port", "8000"]
