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
from sp5lib import calculations as calc

from .._paths import backend_dir
from ..dependencies import (
    _logger,
    get_db,
    require_admin,
    require_planer,
)

router = APIRouter(prefix="/api/work-time-rules", tags=["work-time-rules"])

# ── Storage ───────────────────────────────────────────────────────────────────

_DATA_DIR = Path(backend_dir()) / "data"
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
            # Mit Defaults mergen (vorwärtskompatibel)
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


def _employee_plan(
    db, employee_id: int, from_date: date, to_date: date
) -> tuple[list[tuple[date, dict]], list[tuple[date, dict]], list[tuple[date, dict]]]:
    """Plan-Quellen eines Mitarbeiters (Spec 3.4.2): 5MASHI, 5CYASS, 5SPSHI.

    Zyklusdienste werden — wie in der lib-Fassade — von 5MASHI-Einträgen am
    selben Tag verdrängt (Materialisierungs-Override).
    """

    def _dated(rows) -> list[tuple[date, dict]]:
        out = []
        for r in rows:
            if r.get("EMPLOYEEID") != employee_id:
                continue
            try:
                d = calc.to_date(r.get("DATE"))
            except ValueError:  # defektes DBF-Datum → überspringen
                continue
            if d is not None and from_date <= d <= to_date:
                out.append((d, r))
        return out

    manual = _dated(db._read("MASHI"))
    special = _dated(db._read("SPSHI"))
    cycle_recs = calc.expand_cycle_assignments(
        [r for r in db._read("CYASS") if r.get("EMPLOYEEID") == employee_id],
        cycles=db._read("CYCLE"),
        cycle_entries=db._read("CYENT"),
        cycle_exceptions=db._read("CYEXC"),
        von=from_date,
        bis=to_date,
    )
    manual_days = {d for d, _r in manual}
    cycle = []
    for r in cycle_recs:
        d = calc.to_date(r.get("DATE"))
        if d is not None and d not in manual_days:
            cycle.append((d, r))
    return manual, cycle, special


def _collect_day_data(
    db, employee_id: int, from_date: date, to_date: date
) -> tuple[dict[date, float], list[dict]]:
    """Tagesstunden und Dienstblöcke (echte Zeiten) eines Mitarbeiters.

    Spec-Basis statt erfundener Felder (Befund D7): Stunden je Tag über
    DURATION[Ft?7:wd] (3.4.3 Nr. 5/6), 5SPSHI mit SHIFTID ersetzt den
    Normaldienst (3.4.4 Nr. 12), zyklusgeplante MA über 5CYASS-Expansion
    (3.4.2). Zeitblöcke aus STARTEND[Ft?7:wd] bzw. 5SPSHI.STARTEND (bis zu
    drei Teilfenster, Tageswechsel bei Ende <= Start, D-30/D-31); je Dienst
    ein Block über die Spannweite seiner Fenster — Dienste ohne definierte
    Zeiten liefern keinen Block.
    """
    holidays = calc.holiday_calendar(db._read("HOLID"))
    shifts_by_id = {
        int(s["ID"]): s for s in db._read("SHIFT") if s.get("ID") is not None
    }
    manual, cycle, special = _employee_plan(db, employee_id, from_date, to_date)
    replaced = {d for d, r in special if int(r.get("SHIFTID") or 0)}

    day_hours: dict[date, float] = {}
    blocks: list[dict] = []

    def _add_block(d: date, windows: list[tuple[int, int]]) -> None:
        spans = [(s, e if e > s else e + 1440) for s, e in windows if (s, e) != (0, 0)]
        if not spans:
            return
        base = datetime.combine(d, datetime.min.time())
        blocks.append(
            {
                "date": d,
                "start": base + timedelta(minutes=min(s for s, _ in spans)),
                "end": base + timedelta(minutes=max(e for _, e in spans)),
            }
        )

    for d, r in (*manual, *cycle):
        if d in replaced:
            continue
        shift = shifts_by_id.get(int(r.get("SHIFTID") or 0))
        if shift is None:
            continue
        day_hours[d] = day_hours.get(d, 0.0) + calc.shift_hours_on_day(
            shift, d, holidays
        )
        idx = calc.day_index(d, holidays)
        _add_block(d, calc.parse_startend(str(shift.get(f"STARTEND{idx}") or "")))
    for d, r in special:
        day_hours[d] = day_hours.get(d, 0.0) + float(r.get("DURATION") or 0.0)
        _add_block(d, calc.parse_startend(str(r.get("STARTEND") or "")))

    blocks.sort(key=lambda b: (b["start"], b["end"]))
    return day_hours, blocks


def _check_employee(
    db,
    employee_id: int,
    from_date: date,
    to_date: date,
    rules: dict,
) -> list[dict]:
    """Führt alle Regelprüfungen für einen MA aus. Liefert die Verstoß-dicts."""
    violations: list[dict] = []

    if not rules.get("enabled", True):
        return violations

    max_day = float(rules.get("max_hours_per_day", 10))
    max_week = float(rules.get("max_hours_per_week", 48))
    min_rest = float(rules.get("min_rest_hours_between_shifts", 11))
    max_consec = int(rules.get("max_consecutive_days", 6))

    day_hours, shift_blocks = _collect_day_data(db, employee_id, from_date, to_date)

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
            # Montag dieser Woche fürs Datumsfeld bestimmen
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
    for prev, curr in zip(shift_blocks, shift_blocks[1:], strict=False):
        # Tatsächliche Ruhezeit in Stunden zwischen Vor-Ende und Ist-Beginn
        rest_hours = (curr["start"] - prev["end"]).total_seconds() / 3600
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
