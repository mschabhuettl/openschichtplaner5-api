"""Zyklus-Einheit über die API: unit=0 (Tage) beim Ändern durchgereicht."""

from starlette.testclient import TestClient


def test_create_cycle_mit_tages_einheit(admin_client: TestClient):
    res = admin_client.post("/api/shift-cycles", json={"name": "API-Tage-Create", "size_weeks": 14, "unit": 0})
    assert res.status_code == 200, res.text
    body = res.json()
    assert int(body.get("unit", 1)) == 0
    cid = body.get("ID") or body.get("id")
    admin_client.delete(f"/api/shift-cycles/{cid}")


def test_update_cycle_unit_tage(admin_client: TestClient):
    res = admin_client.post("/api/shift-cycles", json={"name": "API-Tagestest", "size_weeks": 10})
    assert res.status_code == 200, res.text
    body = res.json()
    cid = body.get("ID") or body.get("id") or (body.get("cycle") or {}).get("ID")

    res2 = admin_client.put(f"/api/shift-cycles/{cid}", json={
        "name": "API-Tagestest", "size_weeks": 10, "unit": 0,
    })
    assert res2.status_code == 200, res2.text

    cycles = admin_client.get("/api/shift-cycles").json()
    mine = next(c for c in (cycles if isinstance(cycles, list) else cycles.get("cycles", [])) if c.get("ID") == cid or c.get("id") == cid)
    assert int(mine.get("unit", mine.get("UNIT", 1))) == 0

    admin_client.delete(f"/api/shift-cycles/{cid}")
