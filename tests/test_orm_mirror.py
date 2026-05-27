"""Integration tests for the ORM-mirror admin router (api/routers/orm_mirror.py).

Exercises the consumption of libopenschichtplaner5: POST /api/admin/orm/sync runs
the library's ``sync_all`` (lib 1.4.0, all 11 tables) against the bundled DBF
fixtures, then the read endpoints serve the mirrored shifts / leave-types /
workplaces / schedule entries / holidays / periods back through the library
repositories (1.2.0–1.4.0). A temp-file SQLite engine stands in for the mirror DB.
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
    """sync_all (lib 1.4.0) mirrors all 11 tables and reports per-table counts."""
    resp = client.post("/api/admin/orm/sync", headers=_h(admin_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    synced = body["synced"]
    # sync_all reports every supported table.
    assert {
        "employees",
        "groups",
        "group_assignments",
        "shifts",
        "leave_types",
        "workplaces",
        "shift_assignments",
        "special_shifts",
        "absences",
        "holidays",
        "periods",
        "bookings",
        "overtime",
        "leave_entitlements",
        "shift_demand",
        "special_demand",
        "cycles",
        "cycle_assignments",
        "restrictions",
    } <= set(synced)
    # The fixtures contain real rows for these key tables.
    assert synced["shifts"] > 0
    assert synced["shift_assignments"] > 0
    assert synced["group_assignments"] > 0
    assert synced["holidays"] > 0
    assert synced["cycles"] > 0


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


def test_holidays_after_sync(client, admin_token):
    """5HOLID entries are mirrored with DBF-shaped keys (fixtures have 96)."""
    assert client.post("/api/admin/orm/sync", headers=_h(admin_token)).status_code == 200
    resp = client.get("/api/admin/orm/holidays", headers=_h(admin_token))
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert isinstance(rows, list) and len(rows) > 0
    assert {"ID", "DATE", "NAME"} <= set(rows[0])


def test_periods_endpoint_ok(client, admin_token):
    """5PERIO endpoint responds with a list (fixtures have zero rows)."""
    assert client.post("/api/admin/orm/sync", headers=_h(admin_token)).status_code == 200
    resp = client.get("/api/admin/orm/periods", headers=_h(admin_token))
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json(), list)


def test_time_accounting_endpoints_ok(client, admin_token):
    """5BOOK / 5OVER / 5LEAEN endpoints respond with lists (fixtures empty), and
    accept their filter params (lib 1.5.0)."""
    assert client.post("/api/admin/orm/sync", headers=_h(admin_token)).status_code == 200
    assert isinstance(client.get("/api/admin/orm/bookings", headers=_h(admin_token)).json(), list)
    # date-range filter is accepted
    r = client.get(
        "/api/admin/orm/bookings?date_from=2026-01-01&date_to=2026-12-31&employee_id=1",
        headers=_h(admin_token),
    )
    assert r.status_code == 200 and isinstance(r.json(), list)
    assert isinstance(client.get("/api/admin/orm/overtime", headers=_h(admin_token)).json(), list)
    # year filter is accepted on leave-entitlements
    r = client.get(
        "/api/admin/orm/leave-entitlements?year=2026&employee_id=1", headers=_h(admin_token)
    )
    assert r.status_code == 200 and isinstance(r.json(), list)


def test_planning_endpoints_ok(client, admin_token):
    """5SHDEM / 5SPDEM / 5CYCLE / 5CYASS / 5RESTR endpoints respond with lists and
    accept their filter params (lib 1.6.0). Fixtures have cycles; the rest are empty."""
    assert client.post("/api/admin/orm/sync", headers=_h(admin_token)).status_code == 200
    # cycles have fixture rows with DBF-shaped keys
    cycles = client.get("/api/admin/orm/cycles", headers=_h(admin_token)).json()
    assert isinstance(cycles, list) and len(cycles) > 0
    assert {"ID", "NAME"} <= set(cycles[0])
    # the demand/assignment/restriction endpoints respond with lists + accept filters
    assert isinstance(
        client.get(
            "/api/admin/orm/shift-demands?shift_id=1&weekday=0&group_id=1", headers=_h(admin_token)
        ).json(),
        list,
    )
    assert isinstance(
        client.get(
            "/api/admin/orm/special-demands?date_from=2026-01-01&date_to=2026-12-31",
            headers=_h(admin_token),
        ).json(),
        list,
    )
    assert isinstance(
        client.get(
            "/api/admin/orm/cycle-assignments?employee_id=1&cycle_id=1", headers=_h(admin_token)
        ).json(),
        list,
    )
    assert isinstance(
        client.get(
            "/api/admin/orm/restrictions?employee_id=1&shift_id=1", headers=_h(admin_token)
        ).json(),
        list,
    )


def test_endpoints_require_admin(client):
    """Unauthenticated callers are rejected on every endpoint."""
    assert client.post("/api/admin/orm/sync").status_code == 401
    assert client.get("/api/admin/orm/shifts").status_code == 401
    assert client.get("/api/admin/orm/leave-types").status_code == 401
    assert client.get("/api/admin/orm/workplaces").status_code == 401
    assert client.get("/api/admin/orm/shift-assignments").status_code == 401
    assert client.get("/api/admin/orm/special-shifts").status_code == 401
    assert client.get("/api/admin/orm/absences").status_code == 401
    assert client.get("/api/admin/orm/holidays").status_code == 401
    assert client.get("/api/admin/orm/periods").status_code == 401
    assert client.get("/api/admin/orm/bookings").status_code == 401
    assert client.get("/api/admin/orm/overtime").status_code == 401
    assert client.get("/api/admin/orm/leave-entitlements").status_code == 401
    assert client.get("/api/admin/orm/shift-demands").status_code == 401
    assert client.get("/api/admin/orm/special-demands").status_code == 401
    assert client.get("/api/admin/orm/cycles").status_code == 401
    assert client.get("/api/admin/orm/cycle-assignments").status_code == 401
    assert client.get("/api/admin/orm/restrictions").status_code == 401


def test_sync_is_idempotent(client, admin_token):
    """Calling sync twice upserts rather than duplicating rows."""
    first = client.post("/api/admin/orm/sync", headers=_h(admin_token)).json()["synced"]
    second = client.post("/api/admin/orm/sync", headers=_h(admin_token)).json()["synced"]
    assert first["shifts"] == second["shifts"]
    # Row count in the mirror stays stable across re-syncs.
    after = client.get("/api/admin/orm/shifts", headers=_h(admin_token)).json()
    assert len(after) <= first["shifts"]
