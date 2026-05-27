"""Tests that the API response schemas mirror the real DBF/SP5Database keys.

Regression guard for the earlier mismatch where EmployeeResponse/GroupResponse
declared HIDDEN/EMPLOYEENO/GROUPID/CONTRACTHOURS — fields that never exist on the
payload and only surfaced as misleading always-null entries in the OpenAPI schema.
"""

from api.schemas import EmployeeResponse, GroupResponse, ShiftResponse


def test_shift_response_uses_real_keys():
    """5SHIFT.DBF exposes HIDE, not HIDDEN (verified against the fixture)."""
    fields = ShiftResponse.model_fields
    assert "HIDE" in fields  # real DBF key
    assert "HIDDEN" not in fields  # phantom key removed


def test_shift_response_validates_real_record():
    rec = {"ID": 1, "NAME": "Früh", "SHORTNAME": "F", "HIDE": 0, "POSITION": 2}
    m = ShiftResponse(**rec)
    assert m.ID == 1
    assert m.HIDE is False  # int 0 coerces to bool
    assert m.POSITION == 2


def test_group_response_uses_real_keys():
    fields = GroupResponse.model_fields
    assert "HIDE" in fields  # real DBF key
    assert "HIDDEN" not in fields  # phantom key removed


def test_employee_response_uses_real_keys():
    fields = EmployeeResponse.model_fields
    assert {"HIDE", "NUMBER"} <= set(fields)
    for phantom in ("HIDDEN", "EMPLOYEENO", "GROUPID", "WORKPLACEID", "CONTRACTHOURS"):
        assert phantom not in fields, f"{phantom} should not be declared"


def test_group_response_validates_real_record():
    """A group record (HIDE stored as int 0/1) validates and extras pass through."""
    rec = {"ID": 2, "NAME": "Team A", "SHORTNAME": "TA", "HIDE": 0, "POSITION": 1}
    m = GroupResponse(**rec)
    assert m.ID == 2
    assert m.HIDE is False  # int 0 coerces to bool
    # _FlexModel keeps unknown DBF columns
    assert m.model_dump().get("POSITION") == 1


def test_employee_response_validates_real_record():
    rec = {"ID": 40, "NAME": "Buerger", "FIRSTNAME": "Roland", "HIDE": 0, "NUMBER": "12"}
    m = EmployeeResponse(**rec)
    assert m.NUMBER == "12"
    assert m.HIDE is False
