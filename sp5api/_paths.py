"""Resolve the host application's backend resource root.

The API keeps mutable runtime state (JSON document stores, uploads) and finds
host resources (frontend dist, CHANGELOG.md) under one root directory — the
same root sp5lib (libopenschichtplaner5) resolves via the SP5_BACKEND_DIR
environment variable: ``backend/`` in the main application, the repo root in
this repository's dev/test checkout.

The fallback mirrors ``sp5lib._resource_paths``: the directory containing the
sp5api package. That is only correct for an in-tree/source checkout — installed
deployments must set SP5_BACKEND_DIR explicitly (the main app's start.sh,
Dockerfile and CI do).
"""

import os
import shutil


def backend_dir() -> str:
    """Return the host backend root (see module docstring)."""
    env = os.environ.get("SP5_BACKEND_DIR")
    if env:
        return os.path.abspath(env)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def state_dir() -> str:
    """Single, injectable directory for the API's mutable runtime state
    (JSON document stores, queues, counters).

    ``SP5_STATE_DIR`` overrides the location; the default is
    ``backend_dir()/data``. This consolidates the runtime state that previously
    lived in three places (``backend/data``, ``backend/api/data`` and
    ``backend/api``) into one mountable volume. Existing files are migrated
    transparently on first access (see :func:`state_path`), so upgrading an
    existing deployment loses no data even if the old layout is still mounted.
    """
    env = os.environ.get("SP5_STATE_DIR")
    base = os.path.abspath(env) if env else os.path.join(backend_dir(), "data")
    os.makedirs(base, exist_ok=True)
    return base


def _legacy_state_roots() -> list[str]:
    """Former runtime-state locations, checked for one-time migration."""
    bd = backend_dir()
    return [
        os.path.join(bd, "api", "data"),
        os.path.join(bd, "api"),
        os.path.join(bd, "data"),
    ]


def state_path(rel: str) -> str:
    """Resolve a runtime-state file ``rel`` under the consolidated
    :func:`state_dir`. If the target does not exist yet but a file of the same
    relative path lives in a legacy location, it is moved into the consolidated
    directory on first access (no data loss on upgrade)."""
    new = os.path.join(state_dir(), rel)
    if not os.path.exists(new):
        for root in _legacy_state_roots():
            legacy = os.path.join(root, rel)
            if os.path.abspath(legacy) != os.path.abspath(new) and os.path.exists(legacy):
                os.makedirs(os.path.dirname(new) or ".", exist_ok=True)
                try:
                    shutil.move(legacy, new)
                except OSError:
                    pass  # best-effort migration; fall back to the new path
                break
    os.makedirs(os.path.dirname(new) or ".", exist_ok=True)
    return new
