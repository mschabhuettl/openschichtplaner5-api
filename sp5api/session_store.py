"""Steckbarer serverseitiger Session-Store für OpenSchichtplaner5.

Die API führt serverseitig Buch über jede ausgegebene Session (Schlüssel: der
JWT-``sid``-Claim, bzw. ein rohes Token für Legacy-/Dev-Sessions), damit Tokens
widerrufen, ablaufen gelassen und je Benutzer begrenzt werden können.
Historisch lag das in EINEM In-Prozess-``dict`` (``dependencies._sessions``) —
das schließt Multi-Worker-Deployments aus, weil das dict nicht zwischen
Worker-Prozessen geteilt wird.

Dieses Modul führt eine kleine ``SessionStore``-Abstraktion mit zwei Backends ein:

* ``MemorySessionStore`` — der DEFAULT. Umhüllt exakt dasselbe ``_sessions``-
  dict per Referenz: Verhalten byte-identisch wie zuvor, und Code/Tests, die
  ``_sessions`` direkt anfassen, bleiben konsistent.
* ``RedisSessionStore`` — legt jede Session als Redis-Key mit TTL ab und
  pflegt einen Sekundärindex je Benutzer (ein Redis-Set je User-ID), damit die
  Je-Benutzer-Räumung über Worker hinweg funktioniert. ``redis`` wird lazy
  importiert — das Paket behält keine harte Abhängigkeit.

Backend-Wahl via ``SP5_SESSION_BACKEND`` (``memory`` | ``redis``); die
Redis-Verbindungs-URL kommt aus ``SP5_REDIS_URL``.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any


class SessionStore:
    """Schnittstelle des serverseitigen Session-Stores.

    Eine Session ist ein einfaches ``dict`` mit Benutzerdaten plus
    ``expires_at``-Epoch-Float (oder ``None`` für nicht ablaufende Sessions,
    z. B. das Dev-Modus-Token). Der Schlüssel ist die Session-ID — der
    JWT-``sid`` normaler Logins, oder ein roher Token-String bei Legacy/Dev.
    """

    def set(self, session_id: str, data: dict, expires_at: float | None) -> None:
        """Legt ``data`` unter ``session_id`` ab (Ablauf bei ``expires_at``)."""
        raise NotImplementedError

    def get(self, session_id: str) -> dict | None:
        """Liefert die Session-Daten, oder ``None`` wenn fehlend/abgelaufen.

        Implementierungen räumen den Eintrag beim Fund als abgelaufen weg —
        wie das ursprüngliche In-Memory-Verhalten.
        """
        raise NotImplementedError

    def delete(self, session_id: str) -> bool:
        """Entfernt ``session_id``. Liefert ``True``, wenn sie existierte."""
        raise NotImplementedError

    def sessions_for_user(self, user_id: Any) -> list[tuple[str, dict]]:
        """Liefert ``(session_id, data)`` für alle Sessions von ``user_id``."""
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
    """Redis-Backend: ein Key je Session (mit TTL) + Index-Set je Benutzer.

    Layout (Keys mit Präfix, um anderen Redis-Nutzern nicht in die Quere zu kommen):

    * ``<prefix>session:<session_id>`` → JSON-kodierte Session-Daten mit TTL
      aus ``expires_at`` — Redis räumt abgelaufene Sessions selbst.
    * ``<prefix>user:<user_id>`` → SET der Session-IDs des Benutzers für den
      Je-Benutzer-Räumungs-Lookup. Veraltete Mitglieds-IDs (deren Session-Key
      schon abgelaufen/gelöscht ist) werden beim Lesen lazy weggeputzt.
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
        # Auch den Ablauf in der Payload beachten (TTL ist der primäre Schutz,
        # aber ein Test kann expires_at ohne passende TTL setzen): wenn vorbei, räumen.
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
                # Session-Key weg (abgelaufen/widerrufen) — den veralteten Index-Eintrag putzen.
                self._r.srem(self._ukey(user_id), sid)
                continue
            result.append((sid, data))
        return result


def _make_redis_client(url: str):
    """Importiert ``redis`` lazy und baut einen Client für ``url``.

    Import hier (nicht am Modulkopf), damit das Paket keine harte redis-
    Abhängigkeit hat — gebraucht nur, wenn das redis-Backend gewählt ist.
    """
    import redis  # noqa: PLC0415 — lazy by design

    return redis.Redis.from_url(url)


def create_session_store(backing: dict[str, dict], env: dict[str, str] | None = None) -> SessionStore:
    """Baut den per Umgebung gewählten Session-Store.

    ``SP5_SESSION_BACKEND`` (Default ``memory``) wählt das Backend; ``redis``
    aktiviert :class:`RedisSessionStore` mit ``SP5_REDIS_URL`` (Default
    ``redis://localhost:6379/0``). Jeder andere Wert fällt auf memory zurück.

    ``backing`` ist das bestehende In-Prozess-``_sessions``-dict; der
    Memory-Store umhüllt es, damit direkter Zugriff unverändert funktioniert.
    """
    env = os.environ if env is None else env
    backend = (env.get("SP5_SESSION_BACKEND") or "memory").strip().lower()
    if backend == "redis":
        url = (env.get("SP5_REDIS_URL") or "redis://localhost:6379/0").strip()
        return RedisSessionStore(_make_redis_client(url))
    return MemorySessionStore(backing)
