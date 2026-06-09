"""Qualification matrix and stats router (Q084)."""

import logging as _logging
import re

from fastapi import APIRouter, Depends, Query

from ..dependencies import get_db, require_planer

_logger = _logging.getLogger("sp5api")

router = APIRouter()

_QUAL_SEP = re.compile(r"[,;/\n]+")


def _parse_qualifications(note1: str | None) -> list[str]:
    """Parse comma/semicolon/slash-separated qualifications from NOTE1."""
    if not note1:
        return []
    parts = _QUAL_SEP.split(note1)
    return [p.strip() for p in parts if p.strip()]


@router.get(
    "/api/employees/qualification-matrix",
    tags=["Qualifications"],
    summary="Employee qualification matrix",
    description=(
        "Returns a matrix of employees vs. qualifications. "
        "Qualifications are parsed from the NOTE1 field. "
        "Filter by group_id to restrict to a specific group."
    ),
)
def get_qualification_matrix(
    group_id: int | None = Query(None, description="Filter by group ID"),
    _user: dict = Depends(require_planer),
):
    """Return qualification matrix: rows=employees, columns=qualifications."""
    db = get_db()
    employees = db.get_employees(include_hidden=False)

    # Filter by group if requested
    if group_id is not None:
        member_ids = set(db.get_group_members(group_id))
        employees = [e for e in employees if e.get("ID") in member_ids]

    # Build group lookup: employee_id -> group_name
    all_group_members = db.get_all_group_members()
    groups = db.get_groups(include_hidden=True)
    group_name_map = {g["ID"]: g.get("NAME", "") for g in groups}

    # employee_id -> first group name (employees can be in multiple groups)
    emp_group: dict[int, str] = {}
    for gid, member_ids_list in all_group_members.items():
        for eid in member_ids_list:
            if eid not in emp_group:
                emp_group[eid] = group_name_map.get(gid, "")

    # Collect all unique qualifications (sorted)
    all_quals: set[str] = set()
    emp_quals: dict[int, list[str]] = {}
    for e in employees:
        eid = e.get("ID")
        quals = _parse_qualifications(e.get("NOTE1"))
        emp_quals[eid] = quals
        all_quals.update(quals)

    sorted_quals = sorted(all_quals)

    # Build employee rows
    rows = []
    for e in employees:
        eid = e.get("ID")
        firstname = (e.get("FIRSTNAME") or "").strip()
        surname = (e.get("NAME") or "").strip()
        name = f"{firstname} {surname}".strip() if firstname else surname
        emp_q = set(emp_quals.get(eid, []))
        rows.append(
            {
                "id": eid,
                "name": name,
                "group_name": emp_group.get(eid, ""),
                "qualifications": {q: (q in emp_q) for q in sorted_quals},
            }
        )

    return {"qualifications": sorted_quals, "employees": rows}


@router.get(
    "/api/qualifications/stats",
    tags=["Qualifications"],
    summary="Qualification statistics",
    description=(
        "Returns aggregated stats for each qualification: "
        "how many employees have it and which employees."
    ),
)
def get_qualification_stats(
    group_id: int | None = Query(None, description="Filter by group ID"),
    _user: dict = Depends(require_planer),
):
    """Return stats per qualification."""
    db = get_db()
    employees = db.get_employees(include_hidden=False)

    # Filter by group if requested
    if group_id is not None:
        member_ids = set(db.get_group_members(group_id))
        employees = [e for e in employees if e.get("ID") in member_ids]

    total_employees = len(employees)

    # qual_name -> list of {id, name}
    qual_employees: dict[str, list[dict]] = {}

    for e in employees:
        eid = e.get("ID")
        firstname = (e.get("FIRSTNAME") or "").strip()
        surname = (e.get("NAME") or "").strip()
        name = f"{firstname} {surname}".strip() if firstname else surname
        for q in _parse_qualifications(e.get("NOTE1")):
            qual_employees.setdefault(q, []).append({"id": eid, "name": name})

    result = []
    for qual, emp_list in sorted(qual_employees.items()):
        count = len(emp_list)
        percentage = round(count / total_employees * 100, 1) if total_employees else 0.0
        result.append(
            {
                "name": qual,
                "count": count,
                "percentage": percentage,
                "employees": sorted(emp_list, key=lambda x: x["name"]),
            }
        )

    return {"qualifications": result}
