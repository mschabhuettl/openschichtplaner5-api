# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-06-09

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
