"""A9: programmweite Stammdaten-Sortierung über POST /api/reorder/{entity}."""


def test_reorder_groups(planer_client, write_db_path):
    groups = planer_client.get("/api/groups").json()
    if len(groups) < 2:
        import pytest
        pytest.skip("Zu wenige Gruppen für den Reorder-Test")
    ids = [g["ID"] for g in groups]
    reversed_ids = list(reversed(ids))
    r = planer_client.post("/api/reorder/groups", json={"ordered_ids": reversed_ids})
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == len(reversed_ids)
    # Liste kommt jetzt in der neuen Reihenfolge (Cache invalidiert)
    after = [g["ID"] for g in planer_client.get("/api/groups").json()]
    assert after == reversed_ids


def test_reorder_shifts(planer_client, write_db_path):
    shifts = planer_client.get("/api/shifts").json()
    if len(shifts) < 2:
        import pytest
        pytest.skip("Zu wenige Schichten")
    ids = [s["ID"] for s in shifts]
    moved = ids[1:] + ids[:1]  # erste ans Ende
    r = planer_client.post("/api/reorder/shifts", json={"ordered_ids": moved})
    assert r.status_code == 200
    after = [s["ID"] for s in planer_client.get("/api/shifts").json()]
    assert after == moved


def test_reorder_unknown_entity_400(planer_client, write_db_path):
    r = planer_client.post("/api/reorder/frobnicate", json={"ordered_ids": [1]})
    assert r.status_code == 400


def test_reorder_requires_planer(leser_client, write_db_path):
    r = leser_client.post("/api/reorder/groups", json={"ordered_ids": [1, 2]})
    assert r.status_code in (401, 403)
