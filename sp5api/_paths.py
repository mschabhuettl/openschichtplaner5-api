"""Auflösung des Backend-Ressourcen-Roots der Host-Anwendung.

Die API hält veränderlichen Laufzeit-Zustand (JSON-Stores, Uploads) und findet
Host-Ressourcen (Frontend-dist, CHANGELOG.md) unter EINEM Wurzelverzeichnis —
demselben Root, das sp5lib (libopenschichtplaner5) über die Umgebungsvariable
SP5_BACKEND_DIR auflöst: ``backend/`` in der Hauptanwendung, das Repo-Root im
Dev-/Test-Checkout dieses Repos.

Der Fallback spiegelt ``sp5lib._resource_paths``: das Verzeichnis des
sp5api-Pakets. Das stimmt nur für In-Tree-/Quell-Checkouts — installierte
Deployments MÜSSEN SP5_BACKEND_DIR setzen (start.sh, Dockerfile und CI der
Hauptanwendung tun das).
"""

import os
import shutil


def backend_dir() -> str:
    """Liefert das Host-Backend-Root (siehe Modul-Docstring)."""
    env = os.environ.get("SP5_BACKEND_DIR")
    if env:
        return os.path.abspath(env)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def state_dir() -> str:
    """Einziges, injizierbares Verzeichnis für den veränderlichen Laufzeit-Zustand
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
    """Frühere Zustands-Ablageorte, geprüft für die einmalige Migration."""
    bd = backend_dir()
    return [
        os.path.join(bd, "api", "data"),
        os.path.join(bd, "api"),
        os.path.join(bd, "data"),
    ]


def state_path(rel: str) -> str:
    """Löst eine Zustands-Datei ``rel`` unter dem konsolidierten
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
