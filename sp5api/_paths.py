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


def backend_dir() -> str:
    """Return the host backend root (see module docstring)."""
    env = os.environ.get("SP5_BACKEND_DIR")
    if env:
        return os.path.abspath(env)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
