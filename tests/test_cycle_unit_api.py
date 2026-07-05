"""Zyklus-Einheit über die API: unit=0 (Tage) beim Ändern durchgereicht."""

from datetime import date

from starlette.testclient import TestClient


def test_create_cycle_mit_tages_einheit(admin_client: TestClient):
    res = admin_client.post("/api/shift-cycles", json={"name": "API-Tage-Create", "size_weeks": 14, "unit": 0})
    assert res.status_code == 200, res.text
    body = res.json().get("cycle") or res.json()
    assert int(body.get("unit", 1)) == 0
    cid = body.get("ID") or body.get("id")
    admin_client.delete(f"/api/shift-cycles/{cid}")


def test_update_cycle_unit_tage(admin_client: TestClient):
    res = admin_client.post("/api/shift-cycles", json={"name": "API-Tagestest", "size_weeks": 10})
    assert res.status_code == 200, res.text
    body = res.json().get("cycle") or res.json()
    cid = body.get("ID") or body.get("id")

    res2 = admin_client.put(f"/api/shift-cycles/{cid}", json={
        "name": "API-Tagestest", "size_weeks": 10, "unit": 0,
    })
    assert res2.status_code == 200, res2.text

    cycles = admin_client.get("/api/shift-cycles").json()
    mine = next(c for c in (cycles if isinstance(cycles, list) else cycles.get("cycles", [])) if c.get("ID") == cid or c.get("id") == cid)
    assert int(mine.get("unit", mine.get("UNIT", 1))) == 0

    admin_client.delete(f"/api/shift-cycles/{cid}")


def test_generate_mit_tages_zyklus(admin_client):
    """Regressionsschutz api-Ebene: 10-Tage-Modell (unit=0) materialisiert im
    10-Tage-Rhythmus, nicht SIZE*7 (Wine-belegte UNIT-Semantik)."""
    res = admin_client.post("/api/shift-cycles", json={"name": "API-Gen-Tage", "size_weeks": 10, "unit": 0})
    assert res.status_code == 200, res.text
    cid = (res.json().get("cycle") or res.json())["ID"]

    res = admin_client.put(f"/api/shift-cycles/{cid}", json={
        "name": "API-Gen-Tage", "size_weeks": 10, "unit": 0,
        "entries": [{"index": 2, "shift_id": 1}],
    })
    assert res.status_code == 200, res.text

    emp = admin_client.get("/api/employees").json()[0]
    emp_id = emp["ID"]
    res = admin_client.post("/api/shift-cycles/assign", json={
        "employee_id": emp_id, "cycle_id": cid, "start_date": "2031-01-01",
    })
    assert res.status_code == 200, res.text

    res = admin_client.post("/api/schedule/generate", json={
        "year": 2031, "month": 1, "dry_run": True, "respect_restrictions": False,
    })
    assert res.status_code == 200, res.text
    preview = [e for e in res.json().get("preview", []) if e.get("employee_id") == emp_id]
    dates = sorted(e["date"] for e in preview)
    assert dates == ["2031-01-03", "2031-01-13", "2031-01-23"], dates

    admin_client.delete(f"/api/shift-cycles/assign/{emp_id}")
    admin_client.delete(f"/api/shift-cycles/{cid}")


def test_generate_restriction_grade_semantics_api(admin_client: TestClient):
    """api-Ebene end-to-end: die 5RESTR-Grade wirken korrekt in der Auto-Planung.
    Der in lib 1.32.2 ausgelieferte grade-0-Fix war bisher nur lib-seitig getestet;
    hier über POST /api/restrictions + POST /api/schedule/generate: grade 0 („keine")
    blockiert NICHT, grade 2 („nie") blockiert."""
    emp_id = admin_client.get("/api/employees").json()[0]["ID"]
    c = admin_client.post("/api/shift-cycles", json={"name": "GradeAPI", "size_weeks": 1}).json()
    cid = (c.get("cycle") or c)["ID"]
    try:
        admin_client.put(f"/api/shift-cycles/{cid}", json={
            "name": "GradeAPI", "size_weeks": 1,
            "entries": [{"index": i, "shift_id": 1} for i in range(5)],  # Mo-Fr
        })
        admin_client.post("/api/shift-cycles/assign", json={
            "employee_id": emp_id, "cycle_id": cid, "start_date": "2026-03-02"})  # Montag
        # grade 0 auf Mittwoch (Tagindex 2), grade 2 auf Donnerstag (3) — beide
        # innerhalb des 38,5h-Wochenbudgets (Freitag wäre der erschöpfende 5. Tag).
        for wd, grade in ((2, 0), (3, 2)):
            r = admin_client.post("/api/restrictions", json={
                "employee_id": emp_id, "shift_id": 1, "weekday": wd, "grade": grade})
            assert r.status_code == 200, r.text
            assert r.json()["record"]["RESTRICT"] == grade  # grade wird persistiert

        gen = admin_client.post("/api/schedule/generate", json={
            "year": 2026, "month": 3, "dry_run": True, "respect_restrictions": True})
        assert gen.status_code == 200, gen.text
        pv = [e for e in gen.json()["preview"] if e["employee_id"] == emp_id]
        wed = [e["status"] for e in pv if date.fromisoformat(e["date"]).weekday() == 2]
        thu = [e["status"] for e in pv if date.fromisoformat(e["date"]).weekday() == 3]
        assert wed and all(s == "new" for s in wed), wed  # grade 0 blockiert NICHT
        assert thu and all(s == "restricted" for s in thu), thu  # grade 2 blockiert
        assert gen.json()["skipped_restriction"] == len(thu)
    finally:
        admin_client.delete(f"/api/restrictions/{emp_id}/1")
        admin_client.delete(f"/api/shift-cycles/assign/{emp_id}")
        admin_client.delete(f"/api/shift-cycles/{cid}")
