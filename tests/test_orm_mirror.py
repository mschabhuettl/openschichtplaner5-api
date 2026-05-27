"""Integration tests for the ORM-mirror admin router (api/routers/orm_mirror.py).

Exercises the consumption of libopenschichtplaner5 1.2.0: POST /api/admin/orm/sync
runs the library's DBF→ORM sync against the bundled DBF fixtures, then the
read endpoints serve the mirrored shifts / leave-types / workplaces back through
the new 1.2.0 repositories. A temp-file SQLite engine stands in for the mirror DB.
"""

import os
import secrets

import api.routers.orm_mirror as orm_mirror
import pytest
from api.main import _sessions, app
from starlette.testclient import TestClient

_FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


@pytest.fixture
def orm_engine(tmp_path):
    """Temp-file SQLite engine (survives across the sync's own session)."""
    from sp5lib.orm import get_engine, init_db

    engine = get_engine(f"sqlite:///{tmp_path / 'mirror.db'}")
    init_db(engine)
    return engine


@pytest.fixture
def client(monkeypatch, orm_engine):
    """TestClient with the mirror engine + DBF source pointed at the fixtures."""
    from sp5lib.orm.base import get_session as orm_get_session

    monkeypatch.setattr(orm_mirror, "_get_orm_engine", lambda: orm_engine)
    monkeypatch.setattr(orm_mirror, "_get_orm_session", lambda: orm_get_session(orm_engine))
    monkeypatch.setattr(orm_mirror, "_daten_path", lambda: _FIXTURES)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def admin_token():
    tok = secrets.token_hex(20)
    _sessions[tok] = {
        "ID": 970,
        "NAME": "orm_admin",
        "role": "Admin",
        "ADMIN": True,
        "RIGHTS": 255,
        "company_id": None,
    }
    yield tok
    _sessions.pop(tok, None)


def _h(tok):
    return {"X-Auth-Token": tok}


def test_sync_returns_per_table_counts(client, admin_token):
    """Sync mirrors the three definition tables and reports non-trivial counts."""
    resp = client.post("/api/admin/orm/sync", headers=_h(admin_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    synced = body["synced"]
    # The three FK-free definition tables are reported.
    assert set(synced) == {"shifts", "leave_types", "workplaces"}
    # The fixtures contain real shift/leave-type/workplace rows.
    assert synced["shifts"] > 0
    assert synced["leave_types"] > 0
    assert synced["workplaces"] > 0


def test_list_shifts_after_sync(client, admin_token):
    """After sync, the shifts read endpoint returns DBF-shaped dicts."""
    assert client.post("/api/admin/orm/sync", headers=_h(admin_token)).status_code == 200
    resp = client.get("/api/admin/orm/shifts", headers=_h(admin_token))
    assert resp.status_code == 200, resp.text
    shifts = resp.json()
    assert isinstance(shifts, list) and len(shifts) > 0
    first = shifts[0]
    # to_dict() exposes the DBF-style keys the rest of the app speaks.
    for key in ("ID", "NAME", "SHORTNAME", "STARTEND0", "DURATION0"):
        assert key in first


def test_list_leave_types_after_sync(client, admin_token):
    assert client.post("/api/admin/orm/sync", headers=_h(admin_token)).status_code == 200
    resp = client.get("/api/admin/orm/leave-types", headers=_h(admin_token))
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert isinstance(rows, list) and len(rows) > 0
    assert {"ID", "NAME", "ENTITLED", "CHARGETYP"} <= set(rows[0])


def test_list_workplaces_after_sync(client, admin_token):
    assert client.post("/api/admin/orm/sync", headers=_h(admin_token)).status_code == 200
    resp = client.get("/api/admin/orm/workplaces", headers=_h(admin_token))
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert isinstance(rows, list) and len(rows) > 0
    assert {"ID", "NAME", "SHORTNAME"} <= set(rows[0])


def test_endpoints_require_admin(client):
    """Unauthenticated callers are rejected on every endpoint."""
    assert client.post("/api/admin/orm/sync").status_code == 401
    assert client.get("/api/admin/orm/shifts").status_code == 401
    assert client.get("/api/admin/orm/leave-types").status_code == 401
    assert client.get("/api/admin/orm/workplaces").status_code == 401


def test_sync_is_idempotent(client, admin_token):
    """Calling sync twice upserts rather than duplicating rows."""
    first = client.post("/api/admin/orm/sync", headers=_h(admin_token)).json()["synced"]
    second = client.post("/api/admin/orm/sync", headers=_h(admin_token)).json()["synced"]
    assert first["shifts"] == second["shifts"]
    # Row count in the mirror stays stable across re-syncs.
    after = client.get("/api/admin/orm/shifts", headers=_h(admin_token)).json()
    assert len(after) <= first["shifts"]
