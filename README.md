# openschichtplaner5-api

[![CI](https://github.com/mschabhuettl/openschichtplaner5-api/actions/workflows/ci.yml/badge.svg)](https://github.com/mschabhuettl/openschichtplaner5-api/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

The REST API behind [**OpenSchichtplaner5**](https://github.com/mschabhuettl/openschichtplaner5) —
a pip-installable FastAPI service over
[**libopenschichtplaner5**](https://github.com/mschabhuettl/libopenschichtplaner5) (`sp5lib`),
serving shift-planning data from the original *Schichtplaner5* FoxPro `.DBF` files or the
SQLite/PostgreSQL mirror: auth/2FA with JWT sessions, employees, schedule, absences,
reports/exports (incl. the original personnel table via `GET /api/personnel-table` and
statistics over a free evaluation period), leave-entitlement administration
(`POST /api/leave-entitlements/forfeit`, annual close), notifications (SSE), webhooks,
iCal feeds and more. All Soll/Ist/demand figures are computed by the `sp5lib`
calculation facade — the API carries no arithmetic of its own.

> **Import name:** the distribution is `openschichtplaner5-api`, but the importable
> package is **`sp5api`** (mirroring `libopenschichtplaner5` → `sp5lib`).
> It was extracted from the main app's `backend/api/` with full git history.

## What's inside

| Module | Purpose |
|---|---|
| `sp5api.main` | The FastAPI application (`sp5api.main:app`) — middlewares, health/metrics, SPA serving |
| `sp5api.routers.*` | One router per domain: `auth`, `employees`, `schedule`, `absences`, `reports`, `notifications`, `availability`, `overtime`, `qualification_matrix`, `recurring_shifts`, `ical`, `webhooks`, `admin`, … |
| `sp5api.dependencies` | Session store, JWT, rate limiting, logging, DB wiring |
| `sp5api.schemas` / `sp5api.types` | Pydantic models / type aliases |
| `sp5api.cache` / `sp5api.rate_limit_store` | Response caching, rate-limit event log |

## Permissions

Roles (`Leser` < `Planer` < `Admin`) gate read/write access; on top of that the
granular **5USER rights of the original (spec 9.6)** are enforced per write
route and exposed as a `permissions` object on `GET /api/auth/me`:
`WDUTIES` (schedule writes), `WABSENCES`, `WOVERTIMES` (hour bookings),
`WNOTES`, `WDEVIATION`, `WCYCLEASS`, `WSWAPONLY` (duty swap), `ADDEMPL`
(opt-in employee creation) and `WPAST` — `WPAST=0` blocks every write with a
date in the past, including the bulk routes (`/api/schedule/bulk`,
`/bulk-group`, `/copy-week`, `/swap`), Einsatzplan and booking writes (for
ID-based updates/deletes the stored record's date counts). The built-in
`Admin` account and the last remaining administrator cannot be demoted or
deleted.

## Installation

```bash
pip install openschichtplaner5-api
```

## Running

```bash
SP5_DB_PATH=/path/to/SP5/Daten python -m uvicorn sp5api.main:app --host 0.0.0.0 --port 8000
```

### Key environment variables

| Variable | Default | Purpose |
|---|---|---|
| `SP5_DB_PATH` | *(set it!)* | Directory with the Schichtplaner5 `.DBF` files |
| `SP5_BACKEND_DIR` | package parent dir | Host-app resource root: `<dir>/data`, `<dir>/api/data`, `<dir>/api/uploads`, alembic config. Shared contract with `sp5lib` — set it in installed deployments |
| `SP5_FRONTEND_DIST` | `<SP5_BACKEND_DIR>/../frontend/dist` | Built SPA to serve at `/` (skipped if absent → API-only mode) |
| `SP5_JWT_SECRET` / `SECRET_KEY` | random per process | JWT signing secret |
| `SP5_DEV_MODE` | off | Dev bypass token — never in production |
| `ALLOWED_ORIGINS` | localhost:5173/8000 | CORS origins (comma-separated) |
| `DB_BACKEND` / `DATABASE_URL` | `dbf` | Switch to the PostgreSQL mirror (via `sp5lib`) |

The full list (rate limits, brute-force lockout, SMTP, logging, password policy …) is
documented in the main app's [`.env.example`](https://github.com/mschabhuettl/openschichtplaner5/blob/main/.env.example).

### Docker

Production-ready multi-stage image (slim runtime, non-root, `HEALTHCHECK` on
`/api/health`, `EXPOSE 8000`; `SP5_DB_PATH=/app/data`, `SP5_BACKEND_DIR=/app/backend`):

```bash
# local operation: DBF dir + optional .env (SECRET_KEY!), see docker-compose.yml
SP5_DBF_DIR=/path/to/SP5/Daten docker compose up --build

# plain build + run
docker build -t openschichtplaner5-api .
docker run --rm -p 8000:8000 -v /path/to/SP5/Daten:/app/data openschichtplaner5-api
```

The current versions (lib 1.7.0 / api 1.2.0) are **not on PyPI** — the build
installs the library from Git `main` by default. Override via build arg, e.g.
`--build-arg LIB_SOURCE="libopenschichtplaner5[postgres]==1.7.0"` once released:

```bash
docker build --build-arg LIB_SOURCE=git+https://github.com/mschabhuettl/libopenschichtplaner5.git@main .
```

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check .
pytest
```

To develop against a local clone of the library instead of the PyPI release:

```bash
pip install -e "../libopenschichtplaner5[postgres]"
```

`data/` and `api/data/` at the repo root are runtime-state seeds (skills, wishes,
notification settings) used by the test suite — the same layout the main app keeps
under `backend/`, resolved via `SP5_BACKEND_DIR`. `tests/fixtures/` holds the DBF
fixture database.

## Releasing

Tag `vX.Y.Z` and push — the [release workflow](.github/workflows/release.yml) builds
sdist+wheel and publishes to PyPI via Trusted Publishing (OIDC).

## License

MIT — see [LICENSE](LICENSE).
