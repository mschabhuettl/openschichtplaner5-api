"""A3: Differenzierte Sichtbarkeit (Spec 9.5.3) — api-Durchsetzung.

Die Golden-DB definiert 5GRACC: Benutzer 253/254/255 sind je auf eine Gruppe
beschränkt. Listen-/Plan-Endpoints dürfen nur deren sichtbare MA zeigen.
"""

import secrets

import pytest
from starlette.testclient import TestClient


def _token(role: str, user_id: int) -> str:
    from sp5api.main import _sessions

    tok = secrets.token_hex(20)
    _sessions[tok] = {
        "ID": user_id,
        "NAME": f"scoped_{user_id}",
        "role": role,
        "ADMIN": role == "Admin",
        "RIGHTS": 1,
    }
    return tok


@pytest.fixture
def scoped_user(write_db_path):
    """Ein Benutzer mit differenzierter Sichtbarkeit aus der Golden-DB."""
    from sp5lib.database import SP5Database

    db = SP5Database(write_db_path)
    gracc = db.get_group_access()
    if not gracc:
        pytest.skip("Golden-DB ohne 5GRACC")
    uid = gracc[0]["user_id"]
    visible = db.get_user_visible_employee_ids(uid)
    if not visible:
        pytest.skip("Kein eingeschränkter Scope ableitbar")
    return uid, visible


def test_employees_list_scoped(scoped_user, write_db_path, app):
    uid, visible = scoped_user
    admin_tok = _token("Admin", 251)
    user_tok = _token("Leser", uid)
    with TestClient(app, raise_server_exceptions=False) as c:
        all_emps = c.get("/api/employees", headers={"X-Auth-Token": admin_tok}).json()
        scoped = c.get("/api/employees", headers={"X-Auth-Token": user_tok}).json()
    all_ids = {e["ID"] for e in all_emps}
    scoped_ids = {e["ID"] for e in scoped}
    assert scoped_ids == set(visible)
    assert scoped_ids < all_ids  # echt eingeschränkt


def test_hidden_employee_returns_404(scoped_user, write_db_path, app):
    uid, visible = scoped_user
    from sp5lib.database import SP5Database

    db = SP5Database(write_db_path)
    hidden = next(
        (e["ID"] for e in db.get_employees() if e["ID"] not in visible), None
    )
    if hidden is None:
        pytest.skip("Kein verborgener MA vorhanden")
    tok = _token("Leser", uid)
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get(f"/api/employees/{hidden}", headers={"X-Auth-Token": tok})
    assert r.status_code == 404


def test_schedule_scoped(scoped_user, write_db_path, app):
    uid, visible = scoped_user
    tok = _token("Leser", uid)
    with TestClient(app, raise_server_exceptions=False) as c:
        sched = c.get(
            "/api/schedule?year=2099&month=1", headers={"X-Auth-Token": tok}
        ).json()
    for e in sched:
        assert e["employee_id"] in visible


def test_admin_unrestricted(scoped_user, write_db_path, app):
    _uid, visible = scoped_user
    tok = _token("Admin", 251)
    with TestClient(app, raise_server_exceptions=False) as c:
        emps = c.get("/api/employees", headers={"X-Auth-Token": tok}).json()
    assert {e["ID"] for e in emps} > set(visible)
