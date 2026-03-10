"""Simple TTL-based in-memory cache for frequently queried DB data.

No external dependencies (no Redis). Thread-safe via threading.Lock.
Cache entries expire after TTL seconds and are invalidated on writes.
"""

import threading
import time
from typing import Any

_lock = threading.Lock()
_store: dict[str, tuple[float, Any]] = {}  # key -> (expires_at, value)

# Default TTL in seconds
DEFAULT_TTL = 60


def get(key: str) -> Any | None:
    """Return cached value if present and not expired, else None."""
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
    """Store a value with the given TTL (seconds)."""
    with _lock:
        _store[key] = (time.monotonic() + ttl, value)


def invalidate(*prefixes: str) -> int:
    """Remove all cache entries whose keys start with any of the given prefixes.

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
    """Return cached value or call factory() to compute, cache, and return it."""
    value = get(key)
    if value is not None:
        return value
    result = factory()
    put(key, result, ttl)
    return result
