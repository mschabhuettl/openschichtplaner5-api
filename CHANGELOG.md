# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.3] - 2026-06-10

### Fixed

- The `SP5_DB_PATH` default is now published into the environment
  (`os.environ.setdefault` in `sp5api.main`, like `SP5_BACKEND_DIR` already
  was) instead of living only in the module-level `DB_PATH` constant. Without
  it, `sp5lib.auto_migrate` (which reads `SP5_DB_PATH` itself) and the admin
  backup endpoints fell back to their own — different — defaults when the
  variable was unset, so the DBF auto-migration checked a different directory
  than the API served.

## [1.1.2] - 2026-06-10

### Fixed

- `requires-python` corrected to `>=3.12`: the package uses PEP-695 generics
  (`class PaginatedResponse[T]` in `sp5api/schemas.py`), which fail with a
  `SyntaxError` on Python 3.11 — pip happily installed 1.1.1 there and the app
  crashed on import (this is what kept the main app's `python:3.11-slim`
  Docker image from ever starting). The 3.11 classifier is gone too.

## [1.1.1] - 2026-06-10

### Fixed

- **Scheduled exports/reports were broken since their introduction:** four lazy
  `from sp5lib.db import get_db` imports (`export_scheduler`, three report
  generators in `scheduled_reports`) referenced a module that has never existed
  in any libopenschichtplaner5 release — every scheduled Excel/CSV export and
  every scheduled report run failed with `ModuleNotFoundError`. They now use
  the package's own `sp5api.dependencies.get_db` (DBF/PostgreSQL per
  `DB_BACKEND`). The unit tests had masked the bug by injecting a fake
  `sp5lib.db` into `sys.modules`; they now patch the real dependency, and new
  regression tests run the generators against the fixture DB with no import
  mocking.
- Same bug class in `GET /api/admin/cache-stats`: `from sp5lib.cache import
  get_cache_stats` (also nonexistent) made the endpoint permanently serve its
  degraded fallback — it now reports the API's own response-cache statistics
  (`sp5api.cache.stats()`), with the fallback kept.

## [1.1.0] - 2026-06-10

First standalone release. The API was extracted from
[openschichtplaner5](https://github.com/mschabhuettl/openschichtplaner5)'s
`backend/api/` — with full git history (`git filter-repo`) — into its own repo,
mirroring how [libopenschichtplaner5](https://github.com/mschabhuettl/libopenschichtplaner5)
was extracted earlier.

### Added

- Packaging as `openschichtplaner5-api` (setuptools `pyproject.toml`), importable
  as **`sp5api`**. Depends on `libopenschichtplaner5[postgres]>=1.6.0` from PyPI.
- CI workflow (ruff + pytest with coverage gate on Python 3.12/3.13) and release
  workflow publishing to PyPI via Trusted Publishing (OIDC), mirroring the library.
- `sp5api._paths.backend_dir()`: all host-app resource paths (`data/`, `api/data/`,
  `api/uploads/`, state JSON files, frontend dist, changelog) now resolve via the
  `SP5_BACKEND_DIR` environment variable — the same contract `sp5lib` already uses —
  instead of `__file__`-relative paths that break for an installed package.
- `SP5_FRONTEND_DIST` environment variable to point the SPA mount at a built
  frontend explicitly (default: `<SP5_BACKEND_DIR>/../frontend/dist`; if the
  directory is absent the API runs in API-only mode, as before).

### Changed

- Import name `api` → `sp5api` everywhere (package, tests, mock patch targets).
- Runtime-state seeds moved out of the package: `api/data/` and `api/uploads/`
  now live at the repo root (the `SP5_BACKEND_DIR` layout), not inside `sp5api/`,
  so wheels ship only code.
