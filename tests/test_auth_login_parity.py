"""Regression tests for the login total-failure fixed in cycle 4.

Covers:
  * original-account login with an empty / short password (MD5 parity) — no
    minimum-length rejection on login;
  * the issued token works as an HttpOnly cookie, an X-Auth-Token header AND a
    standard ``Authorization: Bearer`` header;
  * session persistence across follow-up requests;
  * the demo-user bootstrap (admin/planer/leser, Test1234) and role enforcement.
"""

import hashlib
import json
import os

import pytest
from starlette.testclient import TestClient


def _force_md5_only(db, user_id: int, password: str) -> None:
    """Set a user's DIGEST to MD5(password) and drop any bcrypt sidecar entry.

    Makes the original-account (MD5) login path deterministic regardless of
    test ordering — other tests in the shared session DB may set a bcrypt
    password on the same account.
    """
    from sp5lib.dbf_reader import get_table_fields
    from sp5lib.dbf_writer import find_all_records, update_record

    path = db._table("USER")
    fields = get_table_fields(path)
    for idx, _rec in find_all_records(path, fields, ID=user_id):
        update_record(path, fields, idx, {"DIGEST": hashlib.md5(password.encode()).digest()})
        break
    bpath = db._bcrypt_path()
    if os.path.exists(bpath):
        with open(bpath) as fh:
            data = json.load(fh)
        if data.pop(str(user_id), None) is not None:
            with open(bpath, "w") as fh:
                json.dump(data, fh)


@pytest.fixture
def fresh_client(app, test_db_path):
    """A plain TestClient that performs real logins (no injected token).

    Deterministic accounts: 'Admin' (251) gets the original empty-password MD5
    digest, 'Schmidt' (252) MD5('1') — both MD5-only (no bcrypt sidecar).
    """
    from sp5api.dependencies import get_db

    db = get_db()
    _force_md5_only(db, 251, "")
    _force_md5_only(db, 252, "1")
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


def test_cookie_not_secure_over_plain_http(fresh_client):
    """Cycle 8 regression: over plain HTTP the session cookie must NOT be Secure.

    Browsers silently drop a Secure cookie on a plain-HTTP non-localhost origin
    (the typical self-hosted/Portainer deployment); since the SPA relies solely
    on the HttpOnly cookie, that made login appear to fail. The cookie's Secure
    flag must therefore follow the request scheme. Fails against the old
    ``secure = not _IS_DEV`` behaviour (always Secure in production).
    """
    client, _ = fresh_client
    set_cookie = _login(client, "Admin", "").headers.get("set-cookie", "")
    assert "sp5_token=" in set_cookie
    assert "Secure" not in set_cookie, set_cookie


def test_cookie_secure_when_forwarded_https(fresh_client):
    """Behind an HTTPS terminator (X-Forwarded-Proto: https) the cookie IS Secure."""
    client, _ = fresh_client
    r = client.post(
        "/api/auth/login",
        json={"username": "Admin", "password": ""},
        headers={"X-Forwarded-Proto": "https"},
    )
    set_cookie = r.headers.get("set-cookie", "")
    assert "sp5_token=" in set_cookie
    assert "Secure" in set_cookie, set_cookie


def test_login_diagnostics_are_privacy_safe(fresh_client):
    """Failed-login diagnostics must distinguish unknown-user vs. existing-user
    and never expose the password (cycle 8 — operator-debuggable real-DB edge)."""
    _, db = fresh_client
    assert db.login_diagnostics("Admin") == {
        "user_found": True,
        "hidden": False,
        "digest_len": 16,
        "digest_is_md5_shape": True,
        "has_bcrypt": False,
    }
    assert db.login_diagnostics("NoSuchUser") == {"user_found": False}


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
