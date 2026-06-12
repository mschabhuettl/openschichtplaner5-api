"""Pluggable server-side session store for OpenSchichtplaner5.

The API keeps a server-side record of every issued session (keyed by the JWT
``sid`` claim, or by a raw token for legacy/dev sessions) so that tokens can be
revoked, expired and limited per user. Historically this lived in a single
in-process ``dict`` (``dependencies._sessions``), which rules out multi-worker
deployments because the dict is not shared between worker processes.

This module introduces a tiny ``SessionStore`` abstraction with two backends:

* ``MemorySessionStore`` — the DEFAULT. It wraps the very same ``_sessions``
  dict by reference, so behaviour is byte-identical to before and any code or
  test that still pokes ``_sessions`` directly stays consistent.
* ``RedisSessionStore`` — stores each session as a Redis key with a TTL and
  maintains a per-user secondary index (a Redis set per user id) so the
  per-user eviction loop works across workers. ``redis`` is imported lazily so
  the package keeps no hard dependency on it.

Select the backend via ``SP5_SESSION_BACKEND`` (``memory`` | ``redis``); the
Redis connection URL comes from ``SP5_REDIS_URL``.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any


class SessionStore:
    """Interface for the server-side session store.

    A session is a plain ``dict`` of user data plus an ``expires_at`` epoch
    float (or ``None`` for non-expiring sessions, e.g. the dev-mode token). The
    key is the session id — the JWT ``sid`` for normal logins, or a raw token
    string for legacy/dev sessions.
    """

    def set(self, session_id: str, data: dict, expires_at: float | None) -> None:
        """Store ``data`` under ``session_id`` (expiring at ``expires_at``)."""
        raise NotImplementedError

    def get(self, session_id: str) -> dict | None:
        """Return the session data, or ``None`` if missing or expired.

        Implementations purge the entry when it is found expired, matching the
        original in-memory behaviour.
        """
        raise NotImplementedError

    def delete(self, session_id: str) -> bool:
        """Remove ``session_id``. Return ``True`` if it existed."""
        raise NotImplementedError

    def sessions_for_user(self, user_id: Any) -> list[tuple[str, dict]]:
        """Return ``(session_id, data)`` for all sessions of ``user_id``."""
        raise NotImplementedError


class MemorySessionStore(SessionStore):
    """In-process dict backend (default).

    Wraps an existing dict *by reference* so that direct mutations of that dict
    (done by ``dependencies``/``main`` and by the test-suite) and operations
    routed through this store always see the same data.
    """

    def __init__(self, backing: dict[str, dict]):
        self._sessions = backing

    def set(self, session_id: str, data: dict, expires_at: float | None) -> None:
        self._sessions[session_id] = data

    def get(self, session_id: str) -> dict | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        expires_at = session.get("expires_at")
        if expires_at is not None and time.time() > expires_at:
            del self._sessions[session_id]
            return None
        return session

    def delete(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None

    def sessions_for_user(self, user_id: Any) -> list[tuple[str, dict]]:
        return [(sid, s) for sid, s in self._sessions.items() if s.get("ID") == user_id]


class RedisSessionStore(SessionStore):
    """Redis backend: one key per session (with TTL) + a per-user index set.

    Layout (keys are prefixed to avoid clashing with other Redis users):

    * ``<prefix>session:<session_id>`` → JSON-encoded session data, with a TTL
      derived from ``expires_at`` so Redis evicts expired sessions for us.
    * ``<prefix>user:<user_id>`` → a SET of the session ids for that user,
      enabling the per-user eviction lookup. Stale member ids (whose session
      key has already expired/been deleted) are pruned lazily on read.
    """

    def __init__(self, client, prefix: str = "sp5:"):
        self._r = client
        self._prefix = prefix

    # ── key helpers ──────────────────────────────────────────────
    def _skey(self, session_id: str) -> str:
        return f"{self._prefix}session:{session_id}"

    def _ukey(self, user_id: Any) -> str:
        return f"{self._prefix}user:{user_id}"

    def set(self, session_id: str, data: dict, expires_at: float | None) -> None:
        payload = json.dumps(data, default=str)
        ttl = None
        if expires_at is not None:
            ttl = int(expires_at - time.time())
            if ttl <= 0:
                # Already expired — don't store it (mirrors get() purging).
                self.delete(session_id)
                return
        self._r.set(self._skey(session_id), payload, ex=ttl)
        user_id = data.get("ID")
        if user_id is not None:
            self._r.sadd(self._ukey(user_id), session_id)

    def get(self, session_id: str) -> dict | None:
        raw = self._r.get(self._skey(session_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        # Honour the in-payload expiry too (TTL is the primary guard, but a test
        # may set expires_at without a matching TTL): purge if past.
        expires_at = data.get("expires_at")
        if expires_at is not None and time.time() > expires_at:
            self.delete(session_id)
            return None
        return data

    def delete(self, session_id: str) -> bool:
        raw = self._r.get(self._skey(session_id))
        existed = raw is not None
        if raw is not None:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            user_id = json.loads(raw).get("ID")
            if user_id is not None:
                self._r.srem(self._ukey(user_id), session_id)
        self._r.delete(self._skey(session_id))
        return existed

    def sessions_for_user(self, user_id: Any) -> list[tuple[str, dict]]:
        members = self._r.smembers(self._ukey(user_id))
        result: list[tuple[str, dict]] = []
        for member in members:
            sid = member.decode("utf-8") if isinstance(member, bytes) else member
            data = self.get(sid)
            if data is None:
                # Session key gone (expired/revoked) — prune the stale index entry.
                self._r.srem(self._ukey(user_id), sid)
                continue
            result.append((sid, data))
        return result


def _make_redis_client(url: str):
    """Lazily import ``redis`` and build a client for ``url``.

    Imported here (not at module top) so the package has no hard dependency on
    redis — it is only required when the redis backend is actually selected.
    """
    import redis  # noqa: PLC0415 — lazy by design

    return redis.Redis.from_url(url)


def create_session_store(backing: dict[str, dict], env: dict[str, str] | None = None) -> SessionStore:
    """Build the session store selected by the environment.

    ``SP5_SESSION_BACKEND`` (default ``memory``) chooses the backend; ``redis``
    enables :class:`RedisSessionStore` using ``SP5_REDIS_URL`` (default
    ``redis://localhost:6379/0``). Any other value falls back to memory.

    ``backing`` is the existing in-process ``_sessions`` dict; the memory store
    wraps it so existing direct-access code keeps working unchanged.
    """
    env = os.environ if env is None else env
    backend = (env.get("SP5_SESSION_BACKEND") or "memory").strip().lower()
    if backend == "redis":
        url = (env.get("SP5_REDIS_URL") or "redis://localhost:6379/0").strip()
        return RedisSessionStore(_make_redis_client(url))
    return MemorySessionStore(backing)
