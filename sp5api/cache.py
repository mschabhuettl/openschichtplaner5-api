"""Einfacher TTL-basierter In-Memory-Cache für häufig gelesene DB-Daten.

Keine externen Abhängigkeiten (kein Redis). Thread-sicher via threading.Lock.
Einträge laufen nach TTL Sekunden ab und werden bei Writes invalidiert.
"""

import threading
import time
from typing import Any

_lock = threading.Lock()
_store: dict[str, tuple[float, Any]] = {}  # key -> (expires_at, value)

# Default TTL in seconds
DEFAULT_TTL = 60


def get(key: str) -> Any | None:
    """Liefert den Cache-Wert, wenn vorhanden und nicht abgelaufen, sonst None."""
    with _lock:
        entry = _store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() > expires_at:
            del _store[key]
            return None
        return value


def put(key: str, value: Any, ttl: float = DEFAULT_TTL) -> None:
    """Legt einen Wert mit der angegebenen TTL (Sekunden) ab."""
    with _lock:
        _store[key] = (time.monotonic() + ttl, value)


def invalidate(*prefixes: str) -> int:
    """Entfernt alle Cache-Einträge, deren Schlüssel mit einem der Präfixe beginnen.

    Returns the number of entries removed.
    """
    with _lock:
        to_delete = [
            k for k in _store if any(k.startswith(p) for p in prefixes)
        ]
        for k in to_delete:
            del _store[k]
        return len(to_delete)


def clear() -> int:
    """Remove all entries. Returns count removed."""
    with _lock:
        n = len(_store)
        _store.clear()
        return n


def stats() -> dict:
    """Return cache statistics."""
    with _lock:
        now = time.monotonic()
        total = len(_store)
        expired = sum(1 for _, (exp, _v) in _store.items() if now > exp)
        return {"total": total, "active": total - expired, "expired": expired}


def get_or_set(key: str, factory, ttl: float = DEFAULT_TTL) -> Any:
    """Liefert den Cache-Wert oder berechnet ihn via factory(), cached und liefert ihn."""
    value = get(key)
    if value is not None:
        return value
    result = factory()
    put(key, result, ttl)
    return result
