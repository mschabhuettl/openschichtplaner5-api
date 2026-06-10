"""Overtime / Underhours tracking router.

Endpoints:
  GET /api/employees/{id}/overtime?year=YYYY&month=MM
  GET /api/overtime/summary?year=YYYY&month=MM&group_id=X

Soll-/Ist-Stunden kommen aus der lib-Fassade (db.get_statistics bzw.
db.get_employee_stats_month, Spec Kap. 3.3/3.4): CALCBASE-Dispatcher,
Feiertage/WORKDAYS-Maske, tagindexkorrekte DURATION, 5SPSHI-Ersetzung,
expandierte 5CYASS, Abwesenheits-Anrechnung (3.5) und 5BOOK-Konten (3.6).
``contract_hours`` bleibt als HRSWEEK-Anzeige aus 5EMPL erhalten.
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import (
    _logger,
    get_db,
    require_planer,
)

router = APIRouter()


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

    stats = db.get_employee_stats_month(emp_id, year, month)

    return {
        "employee_id": emp_id,
        "employee_name": f"{emp.get('NAME', '')}, {emp.get('FIRSTNAME', '')}".strip(", "),
        "employee_short": emp.get("SHORTNAME", ""),
        "year": year,
        "month": month,
        "contract_hours": float(emp.get("HRSWEEK") or 0),
        "expected_hours": stats.get("target_hours", 0.0),
        "actual_hours": stats.get("actual_hours", 0.0),
        "difference": stats.get("difference", 0.0),
        "shifts_count": stats.get("shifts_count", 0),
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
    hrsweek = {
        e["ID"]: float(e.get("HRSWEEK") or 0)
        for e in db.get_employees(include_hidden=False)
    }

    rows = db.get_statistics(year, month, group_id=group_id)
    if group_id is not None and not rows:
        # Return empty list with metadata rather than 404 — group may have no members
        _logger.debug("Overtime summary: group %d has no active members", group_id)

    result = [
        {
            "employee_id": r["employee_id"],
            "employee_name": r["employee_name"],
            "employee_short": r["employee_short"],
            "contract_hours": hrsweek.get(r["employee_id"], 0.0),
            "expected_hours": r["target_hours"],
            "actual_hours": r["actual_hours"],
            "difference": r["overtime_hours"],
            "shifts_count": r["shifts_count"],
        }
        for r in rows
    ]

    # Sort by difference descending (most overtime first)
    result.sort(key=lambda x: x["difference"], reverse=True)

    return {
        "year": year,
        "month": month,
        "group_id": group_id,
        "count": len(result),
        "employees": result,
    }
