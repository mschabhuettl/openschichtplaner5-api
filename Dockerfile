# ==============================================================================
# openschichtplaner5-api — produktionsreifes Multi-Stage-Image
#
# Builder installiert das Paket + libopenschichtplaner5 in ein venv; das
# Runtime-Image (slim, non-root, ohne Build-Tools) übernimmt nur das venv.
#
# Build-Arg LIB_SOURCE: pip-Requirement für die Library. Default ist der
# PyPI-Pin (reproduzierbare Builds). Override-Beispiele:
#   --build-arg LIB_SOURCE=git+https://github.com/mschabhuettl/libopenschichtplaner5.git@main   (Entwicklungs-Stand)
#   --build-arg LIB_SOURCE="libopenschichtplaner5[postgres]==1.15.0"   (künftige Version)
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

ARG LIB_SOURCE="libopenschichtplaner5[postgres]==1.15.0"

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

# curl für den Docker-HEALTHCHECK; gosu für den privilege-drop im Entrypoint
RUN apt-get update && apt-get install -y --no-install-recommends curl gosu && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1001 sp5 && \
    useradd --uid 1001 --gid sp5 --shell /usr/sbin/nologin --create-home sp5

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    SP5_DB_PATH=/app/data \
    SP5_BACKEND_DIR=/app/backend \
    # Auto-Backups in das beschreibbare, gemountete State-Volume legen (sonst
    # landet das Default <SP5_DB_PATH>/../backups auf der read-only rootfs).
    SP5_BACKUP_DIR=/app/backend/backups

# DBF-Mount-Punkt + mutabler Laufzeit-State (JSON-Stores, Uploads, Backups) —
# sp5-owned, damit Named Volumes beim ersten Start beschreibbar geseedet werden.
# Nur die im Repo getrackten Seeds werden kopiert, nie lokaler Laufzeit-State.
COPY --chown=sp5:sp5 data/skills.json data/wishes.json data/notification_settings.json /app/backend/data/
COPY --chown=sp5:sp5 api/data/skills.json /app/backend/api/data/
RUN mkdir -p /app/data /app/backend/api/uploads /app/backend/backups && \
    chown -R sp5:sp5 /app/data /app/backend

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

WORKDIR /app
VOLUME ["/app/data"]
# Kein festes `USER sp5`: der Container startet als root, damit der Entrypoint die
# Schreibrechte am gemounteten Daten-Verzeichnis angleichen kann, und lässt die App
# danach via gosu als non-root (Daten-Eigentümer, sonst uid 1001) laufen. Wer den
# Container mit `--user` startet, umgeht den root-Schritt — dann ist er selbst
# verantwortlich, dass das Daten-Verzeichnis für diesen Benutzer beschreibbar ist.

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=15s \
  CMD curl -f http://localhost:8000/api/health || exit 1
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "sp5api.main:app", "--host", "0.0.0.0", "--port", "8000"]
