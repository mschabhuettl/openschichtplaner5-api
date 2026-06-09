"""Work Time Rules router — Q079.

Endpoints:
  GET  /api/v1/work-time-rules          — get current rules config
  PUT  /api/v1/work-time-rules          — update rules (Admin only)
  POST /api/v1/work-time-rules/check    — check one employee for violations
  POST /api/v1/work-time-rules/check-all — check all employees in a group
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..dependencies import (
    _logger,
    get_db,
    require_admin,
    require_planer,
)

router = APIRouter(prefix="/api/work-time-rules", tags=["work-time-rules"])

# ── Storage ───────────────────────────────────────────────────────────────────

_DATA_DIR = Path(__file__).parent.parent.parent / "data"
_RULES_FILE = _DATA_DIR / "work_time_rules.json"

_DEFAULT_RULES: dict[str, Any] = {
    "max_hours_per_day": 10.0,
    "max_hours_per_week": 48.0,
    "min_rest_hours_between_shifts": 11.0,
    "max_consecutive_days": 6,
    "enabled": True,
}


def _load_rules() -> dict[str, Any]:
    if _RULES_FILE.exists():
        try:
            data = json.loads(_RULES_FILE.read_text())
            # Merge with defaults for forward-compatibility
            merged = dict(_DEFAULT_RULES)
            merged.update(data)
            return merged
        except Exception:
            pass
    return dict(_DEFAULT_RULES)


def _save_rules(rules: dict[str, Any]) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _RULES_FILE.write_text(json.dumps(rules, indent=2))


# ── Pydantic models ───────────────────────────────────────────────────────────


class WorkTimeRules(BaseModel):
    max_hours_per_day: float = Field(default=10.0, ge=0)
    max_hours_per_week: float = Field(default=48.0, ge=0)
    min_rest_hours_between_shifts: float = Field(default=11.0, ge=0)
    max_consecutive_days: int = Field(default=6, ge=0)
    enabled: bool = True


class Violation(BaseModel):
    type: str
    date: str
    employee_id: int
    description: str
    severity: str  # "warning" | "error"
    value: float
    limit: float


class CheckResult(BaseModel):
    violations: list[Violation]
    summary: dict[str, int]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_shift_duration(db, shift_id: int | None) -> float:
    """Return duration (hours) of a shift by its ID."""
    if not shift_id:
        return 0.0
    for s in db._read("SHIFT"):
        if s.get("ID") == shift_id:
            return float(s.get("DURATION0") or s.get("DURATION") or 0)
    return 0.0


def _get_shifts_map(db) -> dict[int, float]:
    """Return {shift_id: duration_hours} for all shifts."""
    result: dict[int, float] = {}
    for s in db._read("SHIFT"):
        sid = s.get("ID")
        if sid is not None:
            result[int(sid)] = float(s.get("DURATION0") or s.get("DURATION") or 0)
    return result


def _collect_work_days(db, employee_id: int, from_date: date, to_date: date) -> dict[date, float]:
    """Return {day: total_hours} for an employee over a date range."""
    shifts_map = _get_shifts_map(db)
    day_hours: dict[date, float] = {}

    from_str = from_date.isoformat()
    to_str = to_date.isoformat()

    # MASHI: regular shift assignments
    for r in db._read("MASHI"):
        if r.get("EMPLOYEEID") != employee_id:
            continue
        d_str = (r.get("DATE") or "")[:10]
        if not d_str or d_str < from_str or d_str > to_str:
            continue
        try:
            d = date.fromisoformat(d_str)
        except ValueError:
            continue
        sid = r.get("SHIFTID")
        hrs = float(shifts_map.get(sid, 0)) if sid else 0.0
        day_hours[d] = day_hours.get(d, 0.0) + hrs

    # SPSHI: special shifts
    for r in db._read("SPSHI"):
        if r.get("EMPLOYEEID") != employee_id:
            continue
        d_str = (r.get("DATE") or "")[:10]
        if not d_str or d_str < from_str or d_str > to_str:
            continue
        try:
            d = date.fromisoformat(d_str)
        except ValueError:
            continue
        hrs = float(r.get("DURATION") or 0)
        day_hours[d] = day_hours.get(d, 0.0) + hrs

    return day_hours


def _collect_shift_times(db, employee_id: int, from_date: date, to_date: date) -> list[dict]:
    """Return list of {date, start_hour, end_hour} for all shifts of an employee.

    Used for rest-time checks. Falls back to 0-based hours when time is unknown.
    """
    from_str = from_date.isoformat()
    to_str = to_date.isoformat()
    shifts_map_full: dict[int, dict] = {}
    for s in db._read("SHIFT"):
        sid = s.get("ID")
        if sid is not None:
            shifts_map_full[int(sid)] = s

    entries = []

    def _parse_time(val) -> float | None:
        """Convert HH:MM or decimal hours string to float hours, or None."""
        if val is None:
            return None
        v = str(val).strip()
        if ":" in v:
            parts = v.split(":")
            try:
                return int(parts[0]) + int(parts[1]) / 60
            except ValueError:
                return None
        try:
            return float(v)
        except ValueError:
            return None

    for r in db._read("MASHI"):
        if r.get("EMPLOYEEID") != employee_id:
            continue
        d_str = (r.get("DATE") or "")[:10]
        if not d_str or d_str < from_str or d_str > to_str:
            continue
        try:
            d = date.fromisoformat(d_str)
        except ValueError:
            continue
        sid = r.get("SHIFTID")
        shift_info = shifts_map_full.get(sid, {}) if sid else {}
        start_h = _parse_time(shift_info.get("STARTTIME") or shift_info.get("START"))
        dur = float(shift_info.get("DURATION0") or shift_info.get("DURATION") or 0)
        if start_h is None:
            start_h = 8.0  # default assumption
        end_h = start_h + dur
        entries.append({"date": d, "start_hour": start_h, "end_hour": end_h, "duration": dur})

    for r in db._read("SPSHI"):
        if r.get("EMPLOYEEID") != employee_id:
            continue
        d_str = (r.get("DATE") or "")[:10]
        if not d_str or d_str < from_str or d_str > to_str:
            continue
        try:
            d = date.fromisoformat(d_str)
        except ValueError:
            continue
        start_h = 8.0
        dur = float(r.get("DURATION") or 0)
        end_h = start_h + dur
        entries.append({"date": d, "start_hour": start_h, "end_hour": end_h, "duration": dur})

    entries.sort(key=lambda e: (e["date"], e["start_hour"]))
    return entries


def _check_employee(
    db,
    employee_id: int,
    from_date: date,
    to_date: date,
    rules: dict,
) -> list[dict]:
    """Run all rule checks for a single employee. Returns list of violation dicts."""
    violations: list[dict] = []

    if not rules.get("enabled", True):
        return violations

    max_day = float(rules.get("max_hours_per_day", 10))
    max_week = float(rules.get("max_hours_per_week", 48))
    min_rest = float(rules.get("min_rest_hours_between_shifts", 11))
    max_consec = int(rules.get("max_consecutive_days", 6))

    day_hours = _collect_work_days(db, employee_id, from_date, to_date)

    # ── Max hours per day ─────────────────────────────────────────
    for d, hrs in sorted(day_hours.items()):
        if hrs > max_day:
            violations.append({
                "type": "max_hours_per_day",
                "date": d.isoformat(),
                "employee_id": employee_id,
                "description": f"Worked {hrs:.1f}h on {d} (max {max_day}h)",
                "severity": "error" if hrs > max_day * 1.1 else "warning",
                "value": hrs,
                "limit": max_day,
            })

    # ── Max hours per week ────────────────────────────────────────
    # Group by ISO week
    week_hours: dict[tuple[int, int], float] = {}
    for d, hrs in day_hours.items():
        iso = d.isocalendar()
        key = (iso[0], iso[1])
        week_hours[key] = week_hours.get(key, 0.0) + hrs

    for (yr, wk), hrs in sorted(week_hours.items()):
        if hrs > max_week:
            # Find Monday of that week for the date field
            mon = date.fromisocalendar(yr, wk, 1)
            violations.append({
                "type": "max_hours_per_week",
                "date": mon.isoformat(),
                "employee_id": employee_id,
                "description": f"Worked {hrs:.1f}h in week {yr}-W{wk:02d} (max {max_week}h)",
                "severity": "error" if hrs > max_week * 1.05 else "warning",
                "value": hrs,
                "limit": max_week,
            })

    # ── Min rest between shifts ───────────────────────────────────
    shift_entries = _collect_shift_times(db, employee_id, from_date, to_date)
    for i in range(1, len(shift_entries)):
        prev = shift_entries[i - 1]
        curr = shift_entries[i]
        # Calculate actual rest in hours between end of prev and start of curr
        prev_end_dt = datetime.combine(prev["date"], datetime.min.time()) + timedelta(hours=prev["end_hour"])
        curr_start_dt = datetime.combine(curr["date"], datetime.min.time()) + timedelta(hours=curr["start_hour"])
        rest_hours = (curr_start_dt - prev_end_dt).total_seconds() / 3600
        if 0 < rest_hours < min_rest:
            violations.append({
                "type": "min_rest_hours_between_shifts",
                "date": curr["date"].isoformat(),
                "employee_id": employee_id,
                "description": (
                    f"Only {rest_hours:.1f}h rest before shift on {curr['date']} "
                    f"(min {min_rest}h required)"
                ),
                "severity": "error" if rest_hours < min_rest * 0.8 else "warning",
                "value": round(rest_hours, 2),
                "limit": min_rest,
            })

    # ── Max consecutive working days ──────────────────────────────
    worked_dates = sorted(day_hours.keys())
    if worked_dates:
        consec = 1
        for i in range(1, len(worked_dates)):
            if (worked_dates[i] - worked_dates[i - 1]).days == 1:
                consec += 1
                if consec > max_consec:
                    violations.append({
                        "type": "max_consecutive_days",
                        "date": worked_dates[i].isoformat(),
                        "employee_id": employee_id,
                        "description": (
                            f"{consec} consecutive working days ending {worked_dates[i]} "
                            f"(max {max_consec})"
                        ),
                        "severity": "warning" if consec <= max_consec + 1 else "error",
                        "value": float(consec),
                        "limit": float(max_consec),
                    })
            else:
                consec = 1

    return violations


def _build_result(violations: list[dict]) -> dict:
    warnings = sum(1 for v in violations if v["severity"] == "warning")
    errors = sum(1 for v in violations if v["severity"] == "error")
    return {
        "violations": violations,
        "summary": {"total": len(violations), "warnings": warnings, "errors": errors},
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("", summary="Get work time rules")
def get_rules(_user: dict = Depends(require_planer)) -> dict:
    return _load_rules()


@router.put("", summary="Update work time rules (Admin only)")
def update_rules(
    rules: WorkTimeRules,
    _user: dict = Depends(require_admin),
) -> dict:
    data = rules.model_dump()
    _save_rules(data)
    _logger.info("Work time rules updated", extra={"event": "rules_updated"})
    return data


@router.post("/check", summary="Check work time rule violations for one employee")
def check_employee(
    employee_id: int = Query(...),
    from_date: date = Query(..., alias="from"),
    to_date: date = Query(..., alias="to"),
    _user: dict = Depends(require_planer),
) -> dict:
    if to_date < from_date:
        raise HTTPException(status_code=422, detail="'to' must be >= 'from'")

    db = get_db()
    # Validate employee exists
    emp = db.get_employee(employee_id)
    if emp is None:
        raise HTTPException(status_code=404, detail=f"Employee {employee_id} not found")

    rules = _load_rules()
    violations = _check_employee(db, employee_id, from_date, to_date, rules)
    return _build_result(violations)


@router.post("/check-all", summary="Check work time rule violations for all employees in a group")
def check_all(
    group_id: int | None = Query(default=None),
    from_date: date = Query(..., alias="from"),
    to_date: date = Query(..., alias="to"),
    _user: dict = Depends(require_planer),
) -> dict:
    if to_date < from_date:
        raise HTTPException(status_code=422, detail="'to' must be >= 'from'")

    db = get_db()
    all_employees = db.get_employees(include_hidden=False)

    if group_id is not None:
        employees = [e for e in all_employees if e.get("GROUPID") == group_id]
        if not employees:
            raise HTTPException(status_code=404, detail=f"No employees found in group {group_id}")
    else:
        employees = list(all_employees)

    rules = _load_rules()
    all_violations: list[dict] = []
    for emp in employees:
        eid = emp.get("ID")
        if eid is None:
            continue
        violations = _check_employee(db, int(eid), from_date, to_date, rules)
        all_violations.extend(violations)

    return _build_result(all_violations)
