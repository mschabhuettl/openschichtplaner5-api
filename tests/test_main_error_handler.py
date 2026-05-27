"""Test the app-wide exception handler in api/main.py: any unhandled exception
must become a sanitized 500 that never leaks the underlying error. Triggered
via /api/stats (which calls get_db().get_stats() unguarded) with get_db patched
to raise."""

import secrets

import api.main as main
from starlette.testclient import TestClient


def test_unhandled_exception_returns_sanitized_500(monkeypatch):
    def _boom():
        raise RuntimeError("kaboom-secret-internal-detail")

    monkeypatch.setattr(main, "get_db", _boom)

    tok = secrets.token_hex(20)
    main._sessions[tok] = {
        "ID": 996,
        "NAME": "err_admin",
        "role": "Admin",
        "ADMIN": True,
        "RIGHTS": 255,
    }
    try:
        # raise_server_exceptions=False so the registered handler produces the response
        client = TestClient(main.app, raise_server_exceptions=False)
        resp = client.get("/api/v1/stats", headers={"X-Auth-Token": tok})
        assert resp.status_code == 500
        assert resp.json()["detail"] == "Interner Serverfehler. Bitte versuche es erneut."
        # the raw exception text must not leak to the client
        assert "kaboom" not in resp.text
    finally:
        main._sessions.pop(tok, None)
