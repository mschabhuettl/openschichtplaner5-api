"""Backend-parametrized tests for the pluggable session store.

The same session lifecycle — register (login) → get → revoke (logout) →
expire → per-user eviction selection — is exercised against BOTH the in-memory
backend and the Redis backend (backed by ``fakeredis``, so no real server is
needed). This proves the Redis backend behaves identically to the default.
"""

import time

import pytest

from sp5api.session_store import (
    MemorySessionStore,
    RedisSessionStore,
    create_session_store,
)


@pytest.fixture(params=["memory", "redis"])
def store(request):
    """Yield a fresh, empty SessionStore for each backend."""
    if request.param == "memory":
        yield MemorySessionStore({})
    else:
        fakeredis = pytest.importorskip("fakeredis")
        client = fakeredis.FakeStrictRedis()
        client.flushall()
        yield RedisSessionStore(client)


def _session(user_id, expires_at, **extra):
    return {"ID": user_id, "expires_at": expires_at, "_session_id": None, **extra}


# ── Basic lifecycle (both backends) ─────────────────────────────────


def test_set_and_get_roundtrip(store):
    data = _session(1, time.time() + 3600, NAME="alice", role="Admin")
    store.set("sid-1", data, data["expires_at"])
    got = store.get("sid-1")
    assert got is not None
    assert got["ID"] == 1
    assert got["NAME"] == "alice"
    assert got["role"] == "Admin"


def test_get_missing_returns_none(store):
    assert store.get("does-not-exist") is None


def test_delete_revokes_session(store):
    data = _session(2, time.time() + 3600)
    store.set("sid-2", data, data["expires_at"])
    assert store.get("sid-2") is not None
    assert store.delete("sid-2") is True
    assert store.get("sid-2") is None
    # Deleting again reports it was already gone.
    assert store.delete("sid-2") is False


def test_non_expiring_session(store):
    """expires_at=None (e.g. dev-mode token) never expires."""
    data = _session(3, None)
    store.set("sid-dev", data, None)
    assert store.get("sid-dev") is not None


# ── Expiry / TTL (both backends) ────────────────────────────────────


def test_get_expired_returns_none_and_purges(store):
    """A session whose expires_at is in the past is treated as gone and purged."""
    data = _session(4, time.time() - 1)
    store.set("sid-exp", data, data["expires_at"])
    assert store.get("sid-exp") is None
    # Purged: it no longer appears in the per-user listing either.
    assert store.sessions_for_user(4) == []


def test_ttl_set_on_redis_keys():
    """The Redis backend sets a positive TTL derived from expires_at."""
    fakeredis = pytest.importorskip("fakeredis")
    client = fakeredis.FakeStrictRedis()
    store = RedisSessionStore(client)
    data = _session(5, time.time() + 100)
    store.set("sid-ttl", data, data["expires_at"])
    ttl = client.ttl("sp5:session:sid-ttl")
    assert 0 < ttl <= 100


def test_redis_skips_already_expired_on_set():
    """Setting an already-expired session is a no-op on Redis (mirrors purge)."""
    fakeredis = pytest.importorskip("fakeredis")
    client = fakeredis.FakeStrictRedis()
    store = RedisSessionStore(client)
    data = _session(6, time.time() - 5)
    store.set("sid-old", data, data["expires_at"])
    assert store.get("sid-old") is None


# ── Per-user lookup / eviction selection (both backends) ────────────


def test_sessions_for_user_filters_by_id(store):
    store.set("a", _session(10, time.time() + 3600), time.time() + 3600)
    store.set("b", _session(10, time.time() + 3600), time.time() + 3600)
    store.set("c", _session(11, time.time() + 3600), time.time() + 3600)
    user10 = store.sessions_for_user(10)
    assert {sid for sid, _ in user10} == {"a", "b"}
    assert {sid for sid, _ in store.sessions_for_user(11)} == {"c"}


def test_per_user_eviction_of_oldest(store):
    """Reproduce the login eviction loop: keep at most N per user, drop oldest."""
    max_sessions = 3
    now = time.time()
    # Five sessions for the same user, increasing expiry (so increasing "age rank").
    for i in range(5):
        exp = now + 1000 + i
        store.set(f"s{i}", _session(20, exp), exp)

    user_sessions = store.sessions_for_user(20)
    assert len(user_sessions) == 5
    # Evict oldest (lowest expires_at) down to the limit, as auth.login does.
    user_sessions.sort(key=lambda x: x[1].get("expires_at") or 0)
    to_evict = user_sessions[: len(user_sessions) - max_sessions]
    for sid, _ in to_evict:
        store.delete(sid)

    remaining = store.sessions_for_user(20)
    assert len(remaining) == max_sessions
    # The newest three survive.
    assert {sid for sid, _ in remaining} == {"s2", "s3", "s4"}


def test_delete_prunes_user_index(store):
    """After deleting a session, it must not resurface in the per-user index."""
    store.set("x", _session(30, time.time() + 3600), time.time() + 3600)
    store.set("y", _session(30, time.time() + 3600), time.time() + 3600)
    store.delete("x")
    assert {sid for sid, _ in store.sessions_for_user(30)} == {"y"}


# ── Backend selection via env ───────────────────────────────────────


def test_create_session_store_defaults_to_memory():
    backing = {}
    s = create_session_store(backing, env={})
    assert isinstance(s, MemorySessionStore)
    # Memory store wraps the backing dict by reference.
    s.set("k", {"ID": 1, "expires_at": None}, None)
    assert backing["k"]["ID"] == 1


def test_create_session_store_memory_explicit():
    s = create_session_store({}, env={"SP5_SESSION_BACKEND": "memory"})
    assert isinstance(s, MemorySessionStore)


def test_create_session_store_unknown_falls_back_to_memory():
    s = create_session_store({}, env={"SP5_SESSION_BACKEND": "bogus"})
    assert isinstance(s, MemorySessionStore)


def test_create_session_store_redis(monkeypatch):
    """SP5_SESSION_BACKEND=redis builds a RedisSessionStore, importing redis lazily."""
    import sp5api.session_store as ss

    created = {}

    def _fake_make_redis(url):
        created["url"] = url
        fakeredis = pytest.importorskip("fakeredis")
        return fakeredis.FakeStrictRedis()

    monkeypatch.setattr(ss, "_make_redis_client", _fake_make_redis)
    s = create_session_store(
        {}, env={"SP5_SESSION_BACKEND": "redis", "SP5_REDIS_URL": "redis://example:6379/2"}
    )
    assert isinstance(s, RedisSessionStore)
    assert created["url"] == "redis://example:6379/2"


def test_no_hard_redis_import_at_module_level():
    """The package must import without redis installed (redis is optional)."""
    import sp5api.session_store as ss

    src = open(ss.__file__, encoding="utf-8").read()
    # `import redis` only appears inside the lazy factory, never at top level.
    top_level = [
        ln for ln in src.splitlines() if ln.startswith("import ") or ln.startswith("from ")
    ]
    assert not any("redis" in ln for ln in top_level)


# ── Full lifecycle through the production dependencies code ──────────
# These run the *actual* create_jwt_token / _is_token_valid /
# _get_session_from_token / invalidate_sessions_for_user against each backend,
# by swapping deps._session_store. This proves the refactor — not just the
# store class — works identically on memory and redis.


@pytest.fixture(params=["memory", "redis"])
def deps_with_backend(request, monkeypatch):
    import sp5api.dependencies as deps

    if request.param == "memory":
        backing = {}
        store = MemorySessionStore(backing)
        monkeypatch.setattr(deps, "_sessions", backing)
    else:
        fakeredis = pytest.importorskip("fakeredis")
        client = fakeredis.FakeStrictRedis()
        client.flushall()
        store = RedisSessionStore(client)
    monkeypatch.setattr(deps, "_session_store", store)
    return deps


def test_full_jwt_lifecycle(deps_with_backend):
    deps = deps_with_backend
    # login → token issued and immediately valid
    tok = deps.create_jwt_token({"ID": 100, "NAME": "u", "role": "Admin"}, time.time() + 3600)
    assert deps._is_token_valid(tok) is True
    sess = deps._get_session_from_token(tok)
    assert sess is not None and sess["ID"] == 100

    # logout/revoke via the session id → token no longer valid
    sid = deps._decode_jwt(tok)["sid"]
    deps._session_store.delete(sid)
    assert deps._is_token_valid(tok) is False
    assert deps._get_session_from_token(tok) is None


def test_expired_session_invalid(deps_with_backend):
    deps = deps_with_backend
    tok = deps.create_jwt_token({"ID": 101, "role": "Planer"}, time.time() - 1)
    # Server-side session already expired → invalid.
    assert deps._is_token_valid(tok) is False


def test_invalidate_sessions_for_user(deps_with_backend):
    deps = deps_with_backend
    exp = time.time() + 3600
    t1 = deps.create_jwt_token({"ID": 200, "role": "Admin"}, exp)
    t2 = deps.create_jwt_token({"ID": 200, "role": "Admin"}, exp)
    deps.create_jwt_token({"ID": 201, "role": "Admin"}, exp)

    removed = deps.invalidate_sessions_for_user(200)
    assert removed == 2
    assert deps._is_token_valid(t1) is False
    assert deps._is_token_valid(t2) is False
    # The other user's session is untouched.
    assert len(deps._session_store.sessions_for_user(201)) == 1


def test_invalidate_keeps_excepted_session(deps_with_backend):
    deps = deps_with_backend
    exp = time.time() + 3600
    keep = deps.create_jwt_token({"ID": 300, "role": "Admin"}, exp)
    drop = deps.create_jwt_token({"ID": 300, "role": "Admin"}, exp)
    keep_sid = deps._decode_jwt(keep)["sid"]

    removed = deps.invalidate_sessions_for_user(300, except_session_id=keep_sid)
    assert removed == 1
    assert deps._is_token_valid(keep) is True
    assert deps._is_token_valid(drop) is False
