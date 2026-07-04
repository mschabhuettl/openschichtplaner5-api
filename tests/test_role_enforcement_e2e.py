"""P0-2 (Sicherheit) End-to-End: die effektive Rolle wird aus dem echten 5USER-
Satz + 5GRACC/5EMACC-Grants aufgelöst UND serverseitig durchgesetzt.

Deckt den Maintainer-Fall ab: ein RIGHTS=2-Konto ohne Schreib-Grant ist Leser
(nicht Planer) und wird bei Schreibversuchen serverseitig mit 403 abgewiesen —
nicht nur ein versteckter Button. Gegenprobe: RIGHTS=2 mit Schreib-Grant = Planer,
passiert das Rollen-Gate. Voller Pfad Login → Rollenauflösung → Enforcement.
"""

import hashlib

import pytest
from sp5lib.database import SP5Database
from sp5lib.dbf_writer import append_record, get_table_fields
from starlette.testclient import TestClient

_PW = "s3cret"


def _seed_role_users(db_path: str) -> None:
    """Legt in der frischen DB-Kopie zwei RIGHTS=2-Konten an:
    reg_leser (nur Lese-Grant) und reg_planer (Schreib-Grant)."""
    db = SP5Database(db_path)
    fp = db._table("USER")
    fields = get_table_fields(fp)
    digest = hashlib.md5(_PW.encode()).digest()

    def add(name, pos):
        rec = {"POSITION": pos, "NAME": name, "DESCRIP": "Gruppenleiter",
               "ADMIN": 0, "DIGEST": digest, "RIGHTS": 2,
               "CATEGORY": " ".join(["1"] * 20), "ADDEMPL": 0,
               "WDUTIES": 1, "WABSENCES": 1, "WOVERTIMES": 1, "WNOTES": 1,
               "WDEVIATION": 1, "WCYCLEASS": 1, "WSWAPONLY": 0, "WPAST": 1,
               "WACCEMWND": 1, "WACCGRWND": 1, "SHOWABS": 0, "SHOWNOTES": 1,
               "SHOWSTATS": 1, "RACCEMWND": 1, "RACCGRWND": 1, "BACKUP": 0,
               "HIDEBARIN": 0, "HIDEBARNO": 0, "ACCVOWND": 1, "ACCADMWND": 0,
               "MINITABLE": 0, "REPORT": " ".join(["1"] * 20), "HIDE": 0,
               "RESERVED": ""}
        append_record(fp, fields, rec, autoid_field="ID")
        return next(u["ID"] for u in SP5Database(db_path)._read("USER")
                    if str(u.get("NAME")) == name)

    leser_id = add("reg_leser", 90)
    planer_id = add("reg_planer", 91)
    # Bestehende Gruppe wiederverwenden; RO-Grant (rights=1) vs. Schreib-Grant (0).
    groups = SP5Database(db_path)._read("GROUP")
    gid = groups[0]["ID"] if groups else 1
    db.set_group_access(leser_id, gid, 1)
    db.set_group_access(planer_id, gid, 0)


def _login(client, username):
    r = client.post("/api/auth/login", json={"username": username, "password": _PW})
    assert r.status_code == 200, r.text
    body = r.json()
    return body["token"], body["user"]["role"]


@pytest.fixture
def seeded(write_db_path):
    _seed_role_users(write_db_path)
    return write_db_path


def test_rights2_readonly_grant_is_leser_and_writes_are_403(seeded, app):
    """reg_leser (RIGHTS=2, nur Lese-Grant): Rolle Leser, Schreibversuche → 403."""
    with TestClient(app, raise_server_exceptions=False) as c:
        token, role = _login(c, "reg_leser")
        assert role == "Leser"
        hdr = {"Authorization": f"Bearer {token}"}
        r1 = c.post("/api/notes", headers=hdr,
                    json={"date": "2026-07-10", "text": "x", "employee_id": 0})
        r2 = c.post("/api/handover", headers=hdr,
                    json={"date": "2026-07-10", "text": "x"})
        assert r1.status_code == 403, r1.text
        assert r2.status_code == 403, r2.text


def test_rights2_write_grant_is_planer_and_passes_role_gate(seeded, app):
    """reg_planer (RIGHTS=2, Schreib-Grant): Rolle Planer, passiert das Rollen-Gate
    (kein 403 durch die Rollenstufe)."""
    with TestClient(app, raise_server_exceptions=False) as c:
        token, role = _login(c, "reg_planer")
        assert role == "Planer"
        hdr = {"Authorization": f"Bearer {token}"}
        r = c.post("/api/handover", headers=hdr,
                   json={"date": "2026-07-10", "text": "x"})
        assert r.status_code != 403, r.text
