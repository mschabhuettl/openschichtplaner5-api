"""Tests für GET /api/schedule/eligible-replacements (Notfallplan-Ersatzsuche)."""

from starlette.testclient import TestClient


class TestEligibleReplacements:
    def test_returns_list_and_filters_to_group(self, sync_client: TestClient):
        """Liefert nur Kandidaten aus dem Bereich des ausgefallenen MA — eine
        echte Teilmenge aller Mitarbeiter."""
        emps = sync_client.get("/api/employees").json()
        shifts = sync_client.get("/api/shifts").json()
        groups = sync_client.get("/api/groups").json()
        # Mitarbeiter mit Gruppenzugehörigkeit suchen.
        absent_id = None
        for g in groups:
            members = sync_client.get(f"/api/groups/{g['ID']}/members")
            if members.status_code == 200 and members.json():
                absent_id = members.json()[0]
                absent_id = absent_id["ID"] if isinstance(absent_id, dict) else absent_id
                break
        if absent_id is None:
            absent_id = emps[0]["ID"]
        shift_id = shifts[0]["ID"]

        res = sync_client.get(
            "/api/schedule/eligible-replacements",
            params={"date": "2026-07-15", "shift_id": shift_id, "absent_employee_id": absent_id},
        )
        assert res.status_code == 200
        cands = res.json()
        assert isinstance(cands, list)
        ids = {c["id"] for c in cands}
        # Der ausgefallene MA ist nie Kandidat.
        assert absent_id not in ids
        # Echte Teilmenge: nicht alle Mitarbeiter sind geeignet.
        assert len(ids) < len([e for e in emps if not e.get("HIDE")])
        for c in cands:
            assert {"id", "name", "shortname"} <= set(c)

    def test_invalid_date_returns_400(self, sync_client: TestClient):
        res = sync_client.get(
            "/api/schedule/eligible-replacements",
            params={"date": "15.07.2026", "shift_id": 1, "absent_employee_id": 1},
        )
        assert res.status_code == 400

    def test_missing_shift_returns_422(self, sync_client: TestClient):
        res = sync_client.get(
            "/api/schedule/eligible-replacements",
            params={"date": "2026-07-15", "absent_employee_id": 1},
        )
        assert res.status_code == 422

    def test_explicit_group_filter_accepted(self, sync_client: TestClient):
        groups = sync_client.get("/api/groups").json()
        shifts = sync_client.get("/api/shifts").json()
        gid = groups[0]["ID"]
        res = sync_client.get(
            "/api/schedule/eligible-replacements",
            params={
                "date": "2026-07-15",
                "shift_id": shifts[0]["ID"],
                "absent_employee_id": 999999,
                "group_id": gid,
            },
        )
        assert res.status_code == 200
        assert isinstance(res.json(), list)
