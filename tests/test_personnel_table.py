"""Tests für GET /api/personnel-table (Parity-Gap C-3, Spec §3.9.2/§3.9.3)."""

import pytest
from starlette.testclient import TestClient

_STANDARD_COLUMNS = (
    "iststunden",
    "sollstunden",
    "saldo",
    "arbeitszeit",
    "abwesenheit_bezahlt",
    "sonntag",
    "feiertag",
    "sonderdienste",
)


class TestPersonnelTable:
    def test_standard_columns_present(self, sync_client: TestClient):
        res = sync_client.get("/api/personnel-table?from=2026-01-01&to=2026-01-31")
        assert res.status_code == 200
        data = res.json()
        assert data["date_from"] == "2026-01-01"
        assert data["one_year"] is False
        assert data["rows"]
        row = data["rows"][0]
        for col in _STANDARD_COLUMNS:
            assert col in row, f"Missing column: {col}"
        assert "shift_counts" in row
        assert "absence_days_by_type" in row
        assert "shifts" in data["columns"] and "leave_types" in data["columns"]

    def test_values_match_statistics_facade(self, sync_client: TestClient):
        """Spec §3.9.2: Ist/Soll/Saldo == GetActualHours/GetNominalHours —
        identisch zu /api/statistics desselben Zeitraums."""
        res = sync_client.get("/api/personnel-table?from=2026-01-01&to=2026-01-31")
        assert res.status_code == 200
        rows = {r["employee_id"]: r for r in res.json()["rows"]}
        stats = sync_client.get("/api/statistics?year=2026&month=1").json()
        assert stats
        for s in stats:
            row = rows[s["employee_id"]]
            assert row["iststunden"] == pytest.approx(s["actual_hours"], abs=0.011)
            assert row["sollstunden"] == pytest.approx(s["target_hours"], abs=0.011)
            assert row["saldo"] == pytest.approx(s["overtime_hours"], abs=0.021)

    def test_shift_counts_match_schedule(self, sync_client: TestClient):
        """Spec §3.9.3 Nr. 4: Einteilungen je Schichtart im Zeitraum."""
        res = sync_client.get("/api/personnel-table?from=2026-01-01&to=2026-01-31")
        rows = {r["employee_id"]: r for r in res.json()["rows"]}
        schedule = sync_client.get("/api/schedule?year=2026&month=1").json()
        counted: dict = {}
        for e in schedule:
            if e.get("kind") == "shift" and e.get("shift_id"):
                emp_counts = counted.setdefault(e["employee_id"], {})
                key = str(e["shift_id"])
                emp_counts[key] = emp_counts.get(key, 0) + 1
        assert any(counted.values())
        for eid, expected in counted.items():
            if eid in rows:
                assert rows[eid]["shift_counts"] == expected

    def test_one_year_period_has_leave_double_value(self, sync_client: TestClient):
        """Spec §3.9.3 Nr. 6: genau ein Kalenderjahr ⇒ Doppelwert je
        anspruchsverbundener Art (genommen/verbleibend)."""
        res = sync_client.get("/api/personnel-table?from=2026-01-01&to=2026-12-31")
        assert res.status_code == 200
        data = res.json()
        assert data["one_year"] is True
        row = data["rows"][0]
        assert "leave_accounts" in row
        for acct in row["leave_accounts"].values():
            assert set(acct) == {"taken", "remaining"}

    def test_group_filter(self, sync_client: TestClient):
        groups = sync_client.get("/api/groups").json()
        target = None
        for g in groups:
            members = sync_client.get(f"/api/groups/{g['ID']}/members").json()
            if members:
                target = (g["ID"], {m["ID"] for m in members})
                break
        if target is None:
            pytest.skip("Keine Gruppe mit Mitgliedern")
        gid, member_ids = target
        res = sync_client.get(
            f"/api/personnel-table?from=2026-01-01&to=2026-01-31&group_id={gid}"
        )
        assert res.status_code == 200
        returned = {r["employee_id"] for r in res.json()["rows"]}
        assert returned.issubset(member_ids)

    def test_invalid_dates(self, sync_client: TestClient):
        res = sync_client.get("/api/personnel-table?from=bad&to=2026-01-31")
        assert res.status_code == 400
        res = sync_client.get("/api/personnel-table?from=2026-02-01&to=2026-01-31")
        assert res.status_code == 400

    def test_missing_params(self, sync_client: TestClient):
        res = sync_client.get("/api/personnel-table")
        assert res.status_code == 422
