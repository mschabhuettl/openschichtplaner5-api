"""Overtime / Underhours tracking router.

Endpoints:
  GET /api/employees/{id}/overtime?year=YYYY&month=MM
  GET /api/overtime/summary?year=YYYY&month=MM&group_id=X
"""

import calendar
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import (
    _logger,
    get_db,
    require_planer,
)

router = APIRouter()


# ── Helper ────────────────────────────────────────────────────────────────────


def _count_working_days_mon_fri(year: int, month: int) -> int:
    """Count Mon-Fri days in a given month (no holiday adjustments)."""
    num_days = calendar.monthrange(year, month)[1]
    return sum(
        1
        for d in range(1, num_days + 1)
        if datetime(year, month, d).weekday() < 5
    )


def _calc_overtime(
    emp: dict,
    year: int,
    month: int,
    shifts_map: dict,
) -> dict:
    """Calculate overtime stats for a single employee in a given month.

    Returns a dict with:
      - contract_hours   (weekly hours from HRSWEEK)
      - expected_hours   (contract_hours * working_days / 5)
      - actual_hours     (sum of shift durations)
      - difference       (actual - expected)
      - shifts_count
    """
    contract_hours = float(emp.get("HRSWEEK") or 0)

    working_days = _count_working_days_mon_fri(year, month)
    expected_hours = round(contract_hours * working_days / 5, 2) if contract_hours else 0.0

    db = get_db()
    prefix = f"{year:04d}-{month:02d}"
    emp_id = emp["ID"]

    actual_hours = 0.0
    shifts_count = 0

    for r in db._read("MASHI"):
        d = r.get("DATE", "")
        if d and d.startswith(prefix) and r.get("EMPLOYEEID") == emp_id:
            sid = r.get("SHIFTID")
            hrs = 0.0
            if sid and sid in shifts_map:
                hrs = float(shifts_map[sid].get("DURATION0", 0) or 0)
            actual_hours += hrs
            shifts_count += 1

    for r in db._read("SPSHI"):
        d = r.get("DATE", "")
        if d and d.startswith(prefix) and r.get("EMPLOYEEID") == emp_id:
            hrs = float(r.get("DURATION", 0) or 0)
            actual_hours += hrs
            shifts_count += 1

    actual_hours = round(actual_hours, 2)
    difference = round(actual_hours - expected_hours, 2)

    return {
        "contract_hours": contract_hours,
        "expected_hours": expected_hours,
        "actual_hours": actual_hours,
        "difference": difference,
        "shifts_count": shifts_count,
    }


# ── Single employee ───────────────────────────────────────────────────────────


@router.get(
    "/api/employees/{emp_id}/overtime",
    tags=["Statistics"],
    summary="Overtime / underhours for a single employee",
    description=(
        "Returns Soll vs. Ist hours for a specific employee in a given month. "
        "Positive difference = overtime; negative = underhours. "
        "Requires Planer or Admin role."
    ),
)
def get_employee_overtime(
    emp_id: int,
    year: int = Query(..., ge=2000, le=2100, description="Year (YYYY)"),
    month: int = Query(..., ge=1, le=12, description="Month (1–12)"),
    _user: dict = Depends(require_planer),
):
    """Return overtime/underhours for one employee for a given month."""
    db = get_db()
    emp = db.get_employee(emp_id)
    if emp is None:
        raise HTTPException(status_code=404, detail=f"Mitarbeiter ID {emp_id} nicht gefunden")

    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="Ungültiger Monat: muss zwischen 1 und 12 liegen")

    shifts_map = {s["ID"]: s for s in db.get_shifts(include_hidden=True)}

    stats = _calc_overtime(emp, year, month, shifts_map)

    return {
        "employee_id": emp_id,
        "employee_name": f"{emp.get('NAME', '')}, {emp.get('FIRSTNAME', '')}".strip(", "),
        "employee_short": emp.get("SHORTNAME", ""),
        "year": year,
        "month": month,
        **stats,
    }


# ── Summary (all employees / group) ─────────────────────────────────────────


@router.get(
    "/api/overtime/summary",
    tags=["Statistics"],
    summary="Overtime summary for all employees (or filtered by group)",
    description=(
        "Returns a ranked list of all employees with their Soll vs. Ist hours for a given month. "
        "Optionally filter by group_id. Sorted by difference descending (most overtime first). "
        "Admin or Planer role required."
    ),
)
def get_overtime_summary(
    year: int = Query(..., ge=2000, le=2100, description="Year (YYYY)"),
    month: int = Query(..., ge=1, le=12, description="Month (1–12)"),
    group_id: int | None = Query(None, description="Optional: filter by group ID"),
    _user: dict = Depends(require_planer),
):
    """Return overtime/underhours summary for all employees in a month."""
    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="Ungültiger Monat: muss zwischen 1 und 12 liegen")

    db = get_db()
    employees = db.get_employees(include_hidden=False)

    if group_id is not None:
        member_ids = set(db.get_group_members(group_id))
        employees = [e for e in employees if e["ID"] in member_ids]
        if not employees:
            # Return empty list with metadata rather than 404 — group may have no members
            _logger.debug("Overtime summary: group %d has no active members", group_id)

    shifts_map = {s["ID"]: s for s in db.get_shifts(include_hidden=True)}

    result = []
    for emp in employees:
        stats = _calc_overtime(emp, year, month, shifts_map)
        result.append(
            {
                "employee_id": emp["ID"],
                "employee_name": f"{emp.get('NAME', '')}, {emp.get('FIRSTNAME', '')}".strip(", "),
                "employee_short": emp.get("SHORTNAME", ""),
                **stats,
            }
        )

    # Sort by difference descending (most overtime first)
    result.sort(key=lambda x: x["difference"], reverse=True)

    return {
        "year": year,
        "month": month,
        "group_id": group_id,
        "count": len(result),
        "employees": result,
    }
