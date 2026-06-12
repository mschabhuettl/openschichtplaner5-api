"""Regression tests for the login total-failure fixed in cycle 4.

Covers:
  * original-account login with an empty / short password (MD5 parity) — no
    minimum-length rejection on login;
  * the issued token works as an HttpOnly cookie, an X-Auth-Token header AND a
    standard ``Authorization: Bearer`` header;
  * session persistence across follow-up requests;
  * the demo-user bootstrap (admin/planer/leser, Test1234) and role enforcement.
"""

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def fresh_client(app, test_db_path):
    """A plain TestClient that performs real logins (no injected token)."""
    from sp5api.dependencies import get_db

    db = get_db()
    # Known-password accounts on the writable copy: original 'Admin' keeps its
    # empty password; give 'Schmidt' a short and another a strong password.
    yield TestClient(app), db


def _login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_original_empty_password_login(fresh_client):
    """Spec/parity: the original 'Admin' account has an empty password."""
    client, db = fresh_client
    assert db.verify_user_password("Admin", "") is not None  # lib accepts it
    r = _login(client, "Admin", "")
    assert r.status_code == 200, r.text
    assert r.json().get("ok") is True
    assert r.json().get("token")


def test_short_password_login(fresh_client):
    """A one-character original password (MD5) must log in."""
    client, db = fresh_client
    # 'Schmidt' in the fixtures carries MD5('1')
    r = _login(client, "Schmidt", "1")
    assert r.status_code == 200, r.text


def test_wrong_password_rejected(fresh_client):
    client, _ = fresh_client
    r = _login(client, "Admin", "definitely-wrong")
    assert r.status_code == 401


def test_token_works_as_bearer_cookie_and_header(app, fresh_client):
    client, _ = fresh_client
    token = _login(client, "Admin", "").json()["token"]

    # Bearer (fresh client, no cookie — proves the header path alone)
    me = TestClient(app).get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, f"Bearer: {me.text}"
    assert me.json().get("NAME") == "Admin"

    # X-Auth-Token (fresh client, no cookie)
    me2 = TestClient(app).get("/api/auth/me", headers={"X-Auth-Token": token})
    assert me2.status_code == 200, f"X-Auth-Token: {me2.text}"

    # Login set the HttpOnly cookie...
    set_cookie = _login(client, "Admin", "").headers.get("set-cookie", "")
    assert "sp5_token=" in set_cookie and "HttpOnly" in set_cookie, set_cookie

    # ...and the cookie is accepted (sent explicitly to bypass the TestClient
    # Secure-over-http transport filter; HTTPS/dev deployments resend it natively)
    me3 = TestClient(app).get("/api/auth/me", headers={"Cookie": f"sp5_token={token}"})
    assert me3.status_code == 200, f"Cookie: {me3.text}"


def test_session_persists_across_requests(fresh_client):
    client, _ = fresh_client
    token = _login(client, "Admin", "").json()["token"]
    hdr = {"Authorization": f"Bearer {token}"}
    for _ in range(3):
        assert client.get("/api/auth/me", headers=hdr).status_code == 200


def test_demo_user_bootstrap_and_login(fresh_client):
    """admin/planer/leser get seeded and log in with Test1234 + correct roles."""
    client, db = fresh_client
    from sp5api.demo_users import ensure_demo_users

    ensure_demo_users(db)
    expected_roles = {"admin": "Admin", "planer": "Planer", "leser": "Leser"}
    for username, role in expected_roles.items():
        r = _login(client, username, "Test1234")
        assert r.status_code == 200, f"{username}: {r.text}"
        token = r.json()["token"]
        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json().get("role") == role, f"{username} role"

    # idempotent: a second bootstrap creates nothing
    assert ensure_demo_users(db) == []
