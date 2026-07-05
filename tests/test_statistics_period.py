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

    def test_free_period_boundaries_inclusive(self, planer_client: TestClient):
        """Der freie Zeitraum [von, bis] zählt Ist-Stunden GENAU im inklusiven
        Intervall: Tage vor `from`/nach `to` fallen raus, Tage EXAKT auf den
        Grenzen zählen. test_partial_period prüft nur `<=` und würde eine
        exklusive-`to`-Regression (letzter Tag fällt aus der Lohnabrechnung)
        nicht bemerken."""
        emp_id = planer_client.get("/api/employees").json()[0]["ID"]
        # 8h-Frühschichten: vor from, ==from, innen, ==to, nach to
        for d in ("2029-08-04", "2029-08-05", "2029-08-07", "2029-08-10", "2029-08-11"):
            r = planer_client.post(
                "/api/schedule",
                json={"employee_id": emp_id, "date": d, "shift_id": 1},
            )
            assert r.status_code == 200, r.text

        def actual(frm, to):
            st = planer_client.get(f"/api/statistics?from={frm}&to={to}").json()
            return next(s for s in st if s["employee_id"] == emp_id)

        # [05..10]: nur 05, 07, 10 (3×8h); 04 und 11 liegen außerhalb.
        span = actual("2029-08-05", "2029-08-10")
        assert span["actual_hours"] == 24.0
        assert span["shifts_count"] == 3
        # Grenzen einzeln inklusiv (Regressionsschutz gegen exklusive Grenze).
        assert actual("2029-08-05", "2029-08-05")["shifts_count"] == 1  # == from
        assert actual("2029-08-10", "2029-08-10")["shifts_count"] == 1  # == to

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


class TestStatisticsYearRange:
    """Extreme Jahre dürfen keinen 500er werfen: ``date(year, month, 1)`` ist auf
    1..9999 beschränkt, und das ungeprüfte Jahr floss bisher direkt hinein
    (`ValueError: year must be in 1..9999`). Regression: `/api/statistics` und
    `/api/statistics/year-summary` validieren das Jahr jetzt → 400 statt 500."""

    @pytest.mark.parametrize(
        "path",
        [
            "/api/statistics?year=0&month=1",
            "/api/statistics?year=99999&month=1",
            "/api/statistics/year-summary?year=0",
            "/api/statistics/year-summary?year=999999",
        ],
    )
    def test_out_of_range_year_is_400_not_500(self, sync_client: TestClient, path: str):
        assert sync_client.get(path).status_code == 400

    def test_valid_year_still_ok(self, sync_client: TestClient):
        assert sync_client.get("/api/statistics?year=2026&month=1").status_code == 200
        assert sync_client.get("/api/statistics/year-summary?year=2026").status_code == 200
