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
    """Sync mirrors the definition + schedule tables and reports counts."""
    resp = client.post("/api/admin/orm/sync", headers=_h(admin_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    synced = body["synced"]
    # Definition tables (1.2.0) + schedule-entry tables (1.3.0) are reported.
    assert set(synced) == {
        "shifts",
        "leave_types",
        "workplaces",
        "shift_assignments",
        "special_shifts",
        "absences",
    }
    # The fixtures contain real definition + schedule rows.
    assert synced["shifts"] > 0
    assert synced["leave_types"] > 0
    assert synced["workplaces"] > 0
    assert synced["shift_assignments"] > 0
    assert synced["absences"] > 0


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


def test_list_shift_assignments_after_sync(client, admin_token):
    """5MASHI schedule entries are mirrored with DBF-shaped keys."""
    assert client.post("/api/admin/orm/sync", headers=_h(admin_token)).status_code == 200
    resp = client.get("/api/admin/orm/shift-assignments", headers=_h(admin_token))
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert isinstance(rows, list) and len(rows) > 0
    assert {"ID", "DATE", "EMPLOYEEID", "SHIFTID"} <= set(rows[0])


def test_shift_assignments_date_range_filter(client, admin_token):
    """date_from/date_to narrow the result to a single day."""
    assert client.post("/api/admin/orm/sync", headers=_h(admin_token)).status_code == 200
    full = client.get("/api/admin/orm/shift-assignments", headers=_h(admin_token)).json()
    assert len(full) > 0
    day = full[0]["DATE"]
    narrowed = client.get(
        f"/api/admin/orm/shift-assignments?date_from={day}&date_to={day}",
        headers=_h(admin_token),
    ).json()
    assert 0 < len(narrowed) <= len(full)
    assert all(r["DATE"] == day for r in narrowed)


def test_absences_after_sync(client, admin_token):
    """5ABSEN entries are mirrored and queryable."""
    assert client.post("/api/admin/orm/sync", headers=_h(admin_token)).status_code == 200
    resp = client.get("/api/admin/orm/absences", headers=_h(admin_token))
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert isinstance(rows, list) and len(rows) > 0
    assert {"ID", "DATE", "EMPLOYEEID"} <= set(rows[0])


def test_special_shifts_endpoint_ok(client, admin_token):
    """5SPSHI endpoint responds with a list (fixtures may have zero rows)."""
    assert client.post("/api/admin/orm/sync", headers=_h(admin_token)).status_code == 200
    resp = client.get("/api/admin/orm/special-shifts", headers=_h(admin_token))
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json(), list)


def test_endpoints_require_admin(client):
    """Unauthenticated callers are rejected on every endpoint."""
    assert client.post("/api/admin/orm/sync").status_code == 401
    assert client.get("/api/admin/orm/shifts").status_code == 401
    assert client.get("/api/admin/orm/leave-types").status_code == 401
    assert client.get("/api/admin/orm/workplaces").status_code == 401
    assert client.get("/api/admin/orm/shift-assignments").status_code == 401
    assert client.get("/api/admin/orm/special-shifts").status_code == 401
    assert client.get("/api/admin/orm/absences").status_code == 401


def test_sync_is_idempotent(client, admin_token):
    """Calling sync twice upserts rather than duplicating rows."""
    first = client.post("/api/admin/orm/sync", headers=_h(admin_token)).json()["synced"]
    second = client.post("/api/admin/orm/sync", headers=_h(admin_token)).json()["synced"]
    assert first["shifts"] == second["shifts"]
    # Row count in the mirror stays stable across re-syncs.
    after = client.get("/api/admin/orm/shifts", headers=_h(admin_token)).json()
    assert len(after) <= first["shifts"]
