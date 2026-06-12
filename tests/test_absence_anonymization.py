"""A2: api-seitige SHOWABS-Sichtbarkeit (Spec 9.5.2 Nr. 2.1, 9.2 Nr. 3, D-67).

Ein Benutzer mit eingeschränktem SHOWABS sieht Abwesenheiten im Dienstplan
anonymisiert (mode 1) bzw. gar nicht (mode 2); Admin/voll (mode 0) sieht alles.
"""

import secrets

import pytest
from starlette.testclient import TestClient


def _token(role: str, showabs_mode: int) -> str:
    from sp5api.main import _sessions

    tok = secrets.token_hex(20)
    _sessions[tok] = {
        "ID": 950,
        "NAME": "anon_probe",
        "role": role,
        "ADMIN": role == "Admin",
        "RIGHTS": 2,
        "SHOWABS_MODE": showabs_mode,
    }
    return tok


@pytest.fixture
def db_with_absence(write_db_path):
    """Lege eine Abwesenheit (Urlaub) für MA 40 an und liefere (year, month)."""
    from sp5lib.database import SP5Database

    db = SP5Database(write_db_path)
    emp = db.get_employees()[0]["ID"]
    lt = db.get_leave_types()[0]["ID"]
    db.add_absence(employee_id=emp, date_str="2099-03-10", leave_type_id=lt)
    return 2099, 3, emp


def _absences(client, year, month, tok):
    r = client.get(
        f"/api/schedule?year={year}&month={month}", headers={"X-Auth-Token": tok}
    )
    assert r.status_code == 200, r.text
    return [e for e in r.json() if e.get("kind") == "absence"]


def test_mode0_shows_real_absence(db_with_absence, app):
    year, month, _ = db_with_absence
    tok = _token("Planer", 0)
    with TestClient(app, raise_server_exceptions=False) as c:
        abs_entries = _absences(c, year, month, tok)
    assert abs_entries, "Abwesenheit sollte sichtbar sein"
    a = abs_entries[0]
    assert a.get("anonymized") is not True
    assert a.get("leave_type_id") is not None


def test_mode1_anonymises(db_with_absence, app):
    year, month, _ = db_with_absence
    tok = _token("Planer", 1)
    with TestClient(app, raise_server_exceptions=False) as c:
        abs_entries = _absences(c, year, month, tok)
    assert abs_entries, "Abwesenheit bleibt sichtbar, nur anonymisiert"
    a = abs_entries[0]
    assert a["anonymized"] is True
    assert a["leave_type_id"] is None
    assert a["display_name"] == "X"  # 5USETT ANOASHORT der Golden-DB
    assert a["leave_name"] == "Abwesend"  # ANOANAME


def test_mode2_hides(db_with_absence, app):
    year, month, _ = db_with_absence
    tok = _token("Planer", 2)
    with TestClient(app, raise_server_exceptions=False) as c:
        abs_entries = _absences(c, year, month, tok)
    assert abs_entries == [], "Abwesenheiten sollten ausgeblendet sein"


def test_admin_always_full(db_with_absence, app):
    """Admin sieht trotz SHOWABS_MODE=2 die echte Abwesenheit (mode forciert 0)."""
    year, month, _ = db_with_absence
    tok = _token("Admin", 2)
    with TestClient(app, raise_server_exceptions=False) as c:
        abs_entries = _absences(c, year, month, tok)
    assert abs_entries and abs_entries[0].get("anonymized") is not True
