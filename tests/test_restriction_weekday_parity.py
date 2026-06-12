"""RESTR.WEEKDAY uses the original day index (D-34): 0=Mon..6=Sun, 7=holiday.

Regression for the cycle-4 fix: the conflict check previously read WEEKDAY as
"0=all days, 1=Mon..7=Sun (ISO)", which both mis-mapped weekdays by one and
treated a Monday restriction (0) as "every day". This verifies a restriction
fires exactly on its weekday and not on others.
"""

import pytest


@pytest.fixture
def client(sync_client):
    return sync_client


def _items(resp):
    data = resp.json()
    if isinstance(data, dict):
        for key in ("items", "data", "results"):
            if key in data:
                return data[key]
    return data


def _first_employee_and_shift(client):
    emp = _items(client.get("/api/employees"))
    shifts = _items(client.get("/api/shifts"))
    return emp[0]["ID"], shifts[0]["ID"]


def test_restriction_fires_only_on_its_weekday(client):
    emp_id, shift_id = _first_employee_and_shift(client)
    # Restrict the employee from this shift on Wednesday (D-34 index 2)
    r = client.post(
        "/api/restrictions",
        json={"employee_id": emp_id, "shift_id": shift_id, "weekday": 2, "reason": "Test"},
    )
    assert r.status_code in (200, 201), r.text

    def is_restriction_block(resp):
        return resp.status_code == 409 and "restriction" in resp.text.lower()

    # 2026-01-07 is a Wednesday → blocked by the restriction
    wed = client.post(
        "/api/schedule",
        json={"employee_id": emp_id, "shift_id": shift_id, "date": "2026-01-07"},
    )
    assert is_restriction_block(wed), f"Wednesday should be restriction-blocked: {wed.text}"

    # 2026-01-05 is a Monday (D-34 index 0) → the Wednesday restriction must NOT fire
    # (any other 409, e.g. a pre-existing assignment, is unrelated to this test)
    mon = client.post(
        "/api/schedule",
        json={"employee_id": emp_id, "shift_id": shift_id, "date": "2026-01-05"},
    )
    assert not is_restriction_block(mon), f"Monday must not be restriction-blocked: {mon.text}"

    # cleanup
    client.delete(
        f"/api/restrictions?employee_id={emp_id}&shift_id={shift_id}&weekday=2"
    )
