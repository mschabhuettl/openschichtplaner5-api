"""Wert-Tests für die Overtime-Endpoints (Parity-Gap A-1).

Die früheren Tests pinnten die entfernte api-eigene Formel
``HRSWEEK * MoFr-Tage / 5`` (Helfer _calc_overtime/_count_working_days_mon_fri).
Seit A-1 delegieren beide Routen an die lib-Fassade; die Tests prüfen jetzt die
Spec-Semantik (Spec §3.3/§3.4): Routen-Werte == Fassaden-Werte, und der
normative Fall Dez. 2014 (CALCBASE=0, HRSDAY 7,7, Mo-Fr, Ft 25./26.12.)
liefert 161,7 Sollstunden statt der alten 177,1.
"""

import pytest
from starlette.testclient import TestClient


def _facade_db():
    from sp5api.dependencies import get_db

    return get_db()


def test_summary_matches_facade_statistics(sync_client: TestClient):
    """Spec §3.3/§3.4: Summary delegiert an db.get_statistics (keine Eigenformel)."""
    year, month = 2026, 1
    res = sync_client.get(f"/api/overtime/summary?year={year}&month={month}")
    assert res.status_code == 200
    by_id = {e["employee_id"]: e for e in res.json()["employees"]}

    stats = _facade_db().get_statistics(year, month)
    assert stats, "Fixture-DB hat Mitarbeiter"
    for s in stats:
        row = by_id[s["employee_id"]]
        assert row["expected_hours"] == pytest.approx(s["target_hours"])
        assert row["actual_hours"] == pytest.approx(s["actual_hours"])
        assert row["difference"] == pytest.approx(s["overtime_hours"])
        assert row["shifts_count"] == s["shifts_count"]


def test_employee_overtime_matches_facade_month_stats(sync_client: TestClient):
    """Spec §3.3/§3.4: Einzel-MA-Route delegiert an db.get_employee_stats_month."""
    db = _facade_db()
    emp = db.get_employees()[0]
    year, month = 2026, 1
    res = sync_client.get(f"/api/employees/{emp['ID']}/overtime?year={year}&month={month}")
    assert res.status_code == 200
    data = res.json()

    mo = db.get_employee_stats_month(emp["ID"], year, month)
    assert data["expected_hours"] == pytest.approx(mo["target_hours"])
    assert data["actual_hours"] == pytest.approx(mo["actual_hours"])
    assert data["difference"] == pytest.approx(mo["difference"])
    assert data["shifts_count"] == mo["shifts_count"]
    assert data["contract_hours"] == pytest.approx(float(emp.get("HRSWEEK") or 0))


def test_normative_december_2014_target(sync_client: TestClient):
    """Spec §3.3.3: Dez. 2014, CALCBASE=0, Mo-Fr, Ft 25./26.12. ⇒ 161,7 h.

    Die alte api-Formel lieferte 177,1 (23 MoFr-Tage * 38,5 / 5) — der
    Regressionsfall aus parity-api.md (A-1).
    """
    db = _facade_db()
    emp = next(
        e
        for e in db.get_employees()
        if int(e.get("CALCBASE") or 0) == 0
        and float(e.get("HRSDAY") or 0) == pytest.approx(7.7)
        and (e.get("WORKDAYS") or "").startswith("1 1 1 1 1 0 0")
    )
    res = sync_client.get(f"/api/employees/{emp['ID']}/overtime?year=2014&month=12")
    assert res.status_code == 200
    data = res.json()
    assert data["expected_hours"] == pytest.approx(161.7)
    assert data["expected_hours"] != pytest.approx(177.1)
    assert data["difference"] == pytest.approx(
        round(data["actual_hours"] - data["expected_hours"], 2), abs=0.011
    )
