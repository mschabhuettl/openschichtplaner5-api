"""Tests für den freien Auswertungszeitraum von /api/statistics
(Parity-Gap C-1, Spec §3.9.1)."""

import pytest
from starlette.testclient import TestClient


class TestStatisticsFreePeriod:
    def test_period_equals_month_default(self, sync_client: TestClient):
        """from/to über einen ganzen Monat == year/month-Komfortform."""
        by_month = sync_client.get("/api/statistics?year=2026&month=1").json()
        by_period = sync_client.get(
            "/api/statistics?from=2026-01-01&to=2026-01-31"
        ).json()
        assert by_period == by_month

    def test_partial_period(self, sync_client: TestClient):
        """Spec §3.9.1: Teilzeitraum liefert anteiliges Soll/Ist."""
        full = {
            s["employee_id"]: s
            for s in sync_client.get("/api/statistics?year=2026&month=1").json()
        }
        res = sync_client.get("/api/statistics?from=2026-01-01&to=2026-01-07")
        assert res.status_code == 200
        partial = res.json()
        assert partial
        for s in partial:
            f = full[s["employee_id"]]
            assert s["target_hours"] <= f["target_hours"] + 0.01
            assert s["actual_hours"] <= f["actual_hours"] + 0.01
            assert s["shifts_count"] <= f["shifts_count"]
        # Mindestens ein MA hat im Teilzeitraum weniger Soll als im Monat
        assert any(
            s["target_hours"] < full[s["employee_id"]]["target_hours"]
            for s in partial
        )

    def test_period_with_group_filter(self, sync_client: TestClient):
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
            f"/api/statistics?from=2026-01-01&to=2026-01-31&group_id={gid}"
        )
        assert res.status_code == 200
        assert {s["employee_id"] for s in res.json()}.issubset(member_ids)

    def test_one_sided_period_rejected(self, sync_client: TestClient):
        assert (
            sync_client.get("/api/statistics?from=2026-01-01").status_code == 400
        )
        assert sync_client.get("/api/statistics?to=2026-01-31").status_code == 400

    def test_invalid_period_rejected(self, sync_client: TestClient):
        res = sync_client.get("/api/statistics?from=bad&to=2026-01-31")
        assert res.status_code == 400
        res = sync_client.get("/api/statistics?from=2026-02-01&to=2026-01-31")
        assert res.status_code == 400
