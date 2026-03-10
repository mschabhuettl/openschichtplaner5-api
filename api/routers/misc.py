"""Misc router: notes, wishes, handover, swap-requests, changelog, search, access."""


from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..dependencies import (
    _sanitize_500,
    get_db,
    limiter,
    require_admin,
    require_auth,
    require_planer,
)
from ..schemas import paginate
from .events import broadcast
from .notifications import create_notification
from .schedule import SwapShiftsRequest, swap_shifts

router = APIRouter()


# ── Notes ─────────────────────────────────────────────────────


@router.get(
    "/api/notes",
    tags=["Notes"],
    summary="List notes",
    description="Return shift notes, optionally filtered by date or employee.",
)
def get_notes(
    date: str | None = Query(None, description="Filter by date YYYY-MM-DD"),
    employee_id: int | None = Query(None),
    year: int | None = Query(None, description="Filter by year (use with month)"),
    month: int | None = Query(
        None, description="Filter by month 1-12 (use with year)"
    ),
):
    if year is not None and month is not None:
        import calendar as _cal

        last_day = _cal.monthrange(year, month)[1]
        date_from = f"{year:04d}-{month:02d}-01"
        date_to = f"{year:04d}-{month:02d}-{last_day:02d}"
        all_notes = get_db().get_notes(date=None, employee_id=employee_id)
        return [n for n in all_notes if date_from <= (n.get("date") or "") <= date_to]
    return get_db().get_notes(date=date, employee_id=employee_id)


class NoteCreate(BaseModel):
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    # TEXT1/TEXT2: DBF field C len=252, UTF-16-LE → max 125 chars
    text: str = Field(..., min_length=1, max_length=125)
    employee_id: int | None = Field(0, ge=0)
    text2: str | None = Field("", max_length=125)
    # RESERVED (category): DBF field C len=20, UTF-16-LE → max 9 chars
    category: str | None = Field("", max_length=9)


@router.post(
    "/api/notes",
    tags=["Notes"],
    summary="Add note",
    description="Create a new shift note. Requires Planer role.",
)
def add_note(body: NoteCreate, _cur_user: dict = Depends(require_planer)):
    try:
        from datetime import datetime

        datetime.strptime(body.date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Ungültiges Datumsformat, bitte JJJJ-MM-TT verwenden",
        )
    try:
        import html as _html

        result = get_db().add_note(
            date=body.date,
            text=_html.escape(body.text),
            employee_id=body.employee_id or 0,
            text2=_html.escape(body.text2 or ""),
            category=body.category or "",
        )
        broadcast("note_added", {"date": body.date})
        return {"ok": True, "record": result}
    except Exception as e:
        raise _sanitize_500(e)


class NoteUpdate(BaseModel):
    # TEXT1/TEXT2: DBF field C len=252, UTF-16-LE → max 125 chars
    text: str | None = Field(None, min_length=1, max_length=125)
    text2: str | None = Field(None, max_length=125)
    employee_id: int | None = Field(None, ge=0)
    date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    # RESERVED (category): DBF field C len=20, UTF-16-LE → max 9 chars
    category: str | None = Field(None, max_length=9)


@router.put(
    "/api/notes/{note_id}",
    tags=["Notes"],
    summary="Update note",
    description="Update the text or date of an existing shift note. Requires Planer role.",
)
def update_note(
    note_id: int, body: NoteUpdate, _cur_user: dict = Depends(require_planer)
):
    if body.date is not None:
        try:
            from datetime import datetime as _dt

            _dt.strptime(body.date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Ungültiges Datumsformat, bitte JJJJ-MM-TT verwenden",
            )
    try:
        import html as _html

        result = get_db().update_note(
            note_id=note_id,
            text1=_html.escape(body.text) if body.text is not None else None,
            text2=_html.escape(body.text2) if body.text2 is not None else None,
            employee_id=body.employee_id,
            date=body.date,
            category=body.category,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="Notiz nicht gefunden")
        broadcast("note_updated", {"note_id": note_id})
        return {"ok": True, "record": result}
    except HTTPException:
        raise
    except Exception as e:
        raise _sanitize_500(e)


@router.delete(
    "/api/notes/{note_id}",
    tags=["Notes"],
    summary="Delete note",
    description="Permanently delete a shift note by ID. Requires Planer role.",
)
def delete_note(note_id: int, _cur_user: dict = Depends(require_planer)):
    try:
        count = get_db().delete_note(note_id)
        broadcast("note_deleted", {"note_id": note_id})
        return {"ok": True, "deleted": count}
    except Exception as e:
        raise _sanitize_500(e)


@router.get("/api/search", tags=["Employees"], summary="Global search", description="Full-text search across employees, shifts, groups, and other entities.")
def global_search(q: str = Query("", description="Search query")):
    """Global search across employees, shifts, and leave types (absence types).
    Returns up to 20 results per category with fuzzy matching.
    """
    query = q.strip()
    if not query:
        return {"results": [], "query": query}

    db = get_db()

    def _fuzzy_score(text: str, q: str) -> float:
        """Simple fuzzy score: 1.0 for exact match, 0.8 for starts-with,
        0.6 for contains, partial character overlap otherwise (0–0.5)."""
        t = text.lower()
        s = q.lower()
        if t == s:
            return 1.0
        if t.startswith(s):
            return 0.8
        if s in t:
            return 0.6
        # Trigram-style: count overlapping 2-char substrings
        t_bi = {t[i : i + 2] for i in range(len(t) - 1)} if len(t) >= 2 else set()
        s_bi = {s[i : i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else set()
        if t_bi and s_bi:
            overlap = len(t_bi & s_bi) / max(len(t_bi), len(s_bi))
            if overlap > 0.3:
                return overlap * 0.5
        return 0.0

    results = []

    # ── Employees ─────────────────────────────────────────────
    employees = db.get_employees(include_hidden=False)
    for emp in employees:
        name = f"{emp.get('NAME', '')} {emp.get('FIRSTNAME', '')}".strip()
        short = emp.get("SHORTNAME", "") or ""
        number = emp.get("NUMBER", "") or ""
        score = max(
            _fuzzy_score(name, query),
            _fuzzy_score(short, query),
            _fuzzy_score(number, query) * 0.9,
        )
        if score > 0.25:
            results.append(
                {
                    "type": "employee",
                    "id": emp.get("ID"),
                    "title": name,
                    "subtitle": f"Kürzel: {short}" if short else "",
                    "path": "/employees",
                    "icon": "👤",
                    "score": score,
                }
            )

    # ── Shifts ────────────────────────────────────────────────
    shifts = db.get_shifts(include_hidden=False)
    for sh in shifts:
        name = sh.get("NAME", "") or ""
        short = sh.get("SHORTNAME", "") or ""
        score = max(
            _fuzzy_score(name, query),
            _fuzzy_score(short, query) * 0.9,
        )
        if score > 0.25:
            results.append(
                {
                    "type": "shift",
                    "id": sh.get("ID"),
                    "title": name,
                    "subtitle": f"Kürzel: {short}" if short else "",
                    "path": "/shifts",
                    "icon": "🕐",
                    "score": score,
                }
            )

    # ── Leave Types ───────────────────────────────────────────
    leave_types = db.get_leave_types(include_hidden=False)
    for lt in leave_types:
        name = lt.get("NAME", "") or ""
        short = lt.get("SHORTNAME", "") or ""
        score = max(
            _fuzzy_score(name, query),
            _fuzzy_score(short, query) * 0.9,
        )
        if score > 0.25:
            results.append(
                {
                    "type": "leave_type",
                    "id": lt.get("ID"),
                    "title": name,
                    "subtitle": f"Kürzel: {short}" if short else "",
                    "path": "/leave-types",
                    "icon": "📋",
                    "score": score,
                }
            )

    # ── Groups ────────────────────────────────────────────────
    groups = db.get_groups(include_hidden=False)
    for grp in groups:
        name = grp.get("NAME", "") or ""
        short = grp.get("SHORTNAME", "") or ""
        score = max(
            _fuzzy_score(name, query),
            _fuzzy_score(short, query) * 0.9,
        )
        if score > 0.25:
            results.append(
                {
                    "type": "group",
                    "id": grp.get("ID"),
                    "title": name,
                    "subtitle": f"Kürzel: {short}" if short else "",
                    "path": "/groups",
                    "icon": "🏢",
                    "score": score,
                }
            )

    # Sort by score descending, limit to 30 total
    results.sort(key=lambda x: -(x["score"] or 0))  # type: ignore[operator]  # score is always numeric but typed as mixed dict value
    results = results[:30]
    # Remove internal score field from output
    for r in results:
        del r["score"]

    return {"results": results, "query": query}


# ── Employee / Group Access Rights ───────────────────────────


class EmployeeAccessSet(BaseModel):
    user_id: int = Field(..., gt=0)
    employee_id: int = Field(..., gt=0)
    rights: int = Field(0, ge=0)


class GroupAccessSet(BaseModel):
    user_id: int = Field(..., gt=0)
    group_id: int = Field(..., gt=0)
    rights: int = Field(0, ge=0)


@router.get(
    "/api/employee-access", tags=["Users"], summary="List employee access rules",
    description="Return the employee-level access permissions for a user.",
)
def get_employee_access(
    user_id: int | None = Query(None), _cur_user: dict = Depends(require_admin)
):
    """Get employee-level access restrictions."""
    return get_db().get_employee_access(user_id=user_id)


@router.post(
    "/api/employee-access", tags=["Users"], summary="Create employee access rule",
    description="Set group-based or employee-based access permissions for a user.",
)
def set_employee_access(
    body: EmployeeAccessSet, _cur_user: dict = Depends(require_admin)
):
    """Set employee-level access for a user."""
    try:
        result = get_db().set_employee_access(
            body.user_id, body.employee_id, body.rights
        )
        return {"ok": True, "record": result}
    except Exception as e:
        raise _sanitize_500(e)


@router.delete(
    "/api/employee-access/{access_id}",
    tags=["Users"],
    summary="Delete employee access rule",
    description="Remove an employee access entry.",
)
def delete_employee_access(access_id: int, _cur_user: dict = Depends(require_admin)):
    """Remove an employee access entry."""
    count = get_db().delete_employee_access(access_id)
    if count == 0:
        raise HTTPException(status_code=404, detail="Zugriffseintrag nicht gefunden")
    return {"ok": True, "deleted": access_id}


@router.get("/api/group-access", tags=["Users"], summary="List group access rules", description="Return group access permissions for a specific user.")
def get_group_access(
    user_id: int | None = Query(None), _cur_user: dict = Depends(require_admin)
):
    """Get group-level access restrictions."""
    return get_db().get_group_access(user_id=user_id)


@router.post("/api/group-access", tags=["Users"], summary="Create group access rule", description="Set group-level access for a user.")
def set_group_access(body: GroupAccessSet, _cur_user: dict = Depends(require_admin)):
    """Set group-level access for a user."""
    try:
        result = get_db().set_group_access(body.user_id, body.group_id, body.rights)
        return {"ok": True, "record": result}
    except Exception as e:
        raise _sanitize_500(e)


@router.delete(
    "/api/group-access/{access_id}", tags=["Users"], summary="Delete group access rule",
    description="Remove a group access entry.",
)
def delete_group_access(access_id: int, _cur_user: dict = Depends(require_admin)):
    """Remove a group access entry."""
    count = get_db().delete_group_access(access_id)
    if count == 0:
        raise HTTPException(status_code=404, detail="Zugriffseintrag nicht gefunden")
    return {"ok": True, "deleted": access_id}


# ── Changelog / Aktivitätsprotokoll ─────────────────────────


@router.get("/api/changelog", tags=["Admin"], summary="List audit log entries", description="Return the activity changelog with optional filtering and pagination.")
def get_changelog(
    limit: int = Query(100, description="Max entries to return (applied before pagination)"),
    user: str | None = Query(None, description="Filter by user"),
    entity_type: str | None = Query(None, description="Filter by entity type (employee, schedule, absence, …)"),
    date_from: str | None = Query(None, description="ISO date YYYY-MM-DD"),
    date_to: str | None = Query(None, description="ISO date YYYY-MM-DD"),
    page: int | None = Query(None, ge=1, description="Page number (1-based). Omit for unpaginated list."),
    page_size: int = Query(50, ge=1, le=500, description="Items per page"),
):
    """Return activity log entries from changelog.json."""
    result = get_db().get_changelog(
        limit=limit, user=user, entity_type=entity_type,
        date_from=date_from, date_to=date_to,
    )
    return paginate(result, page, page_size)


class ChangelogEntry(BaseModel):
    user: str
    action: str  # CREATE / UPDATE / DELETE
    entity: str  # employee / shift / schedule / ...
    entity_id: int
    details: str | None = ""


@router.post("/api/changelog", tags=["Admin"], summary="Add audit log entry", description="Manually write an entry to the changelog.")
def log_action(body: ChangelogEntry, _cur_user: dict = Depends(require_planer)):
    """Manually write an entry to the changelog."""
    entry = get_db().log_action(
        user=body.user,
        action=body.action,
        entity=body.entity,
        entity_id=body.entity_id,
        details=body.details or "",
    )
    return entry


# ── Schicht-Wünsche & Sperrtage ─────────────────────────────────


@router.get("/api/wishes", tags=["Self-Service"], summary="List shift wishes")
def get_wishes(
    employee_id: int | None = None,
    year: int | None = None,
    month: int | None = None,
):
    return get_db().get_wishes(employee_id=employee_id, year=year, month=month)


class WishCreate(BaseModel):
    employee_id: int = Field(..., gt=0)
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    wish_type: str = Field(..., pattern=r"^(?i:WUNSCH|SPERRUNG)$")  # WUNSCH | SPERRUNG (case-insensitive)
    shift_id: int | None = Field(None, gt=0)
    note: str | None = Field("", max_length=500)


@router.post("/api/wishes", tags=["Self-Service"], summary="Create shift wish", description="Create a shift wish or blocked day for an employee. Requires Planer role.")
def create_wish(body: WishCreate, _cur_user: dict = Depends(require_planer)):
    wish_type = body.wish_type.upper()
    if wish_type not in ("WUNSCH", "SPERRUNG"):
        raise HTTPException(
            status_code=400, detail="wish_type must be WUNSCH or SPERRUNG"
        )
    try:
        return get_db().add_wish(
            employee_id=body.employee_id,
            date=body.date,
            wish_type=wish_type,
            shift_id=body.shift_id,
            note=body.note or "",
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.delete(
    "/api/wishes/{wish_id}", tags=["Self-Service"], summary="Delete shift wish",
    description="Delete a shift wish by ID. Requires Planer role.",
)
def delete_wish(wish_id: int, _cur_user: dict = Depends(require_planer)):
    deleted = get_db().delete_wish(wish_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Wunsch nicht gefunden")
    return {"deleted": wish_id}


class WishApprove(BaseModel):
    action: str = Field(..., pattern=r"^(approve|reject)$")  # 'approve' | 'reject'
    note: str | None = Field(None, max_length=500)


@router.patch(
    "/api/wishes/{wish_id}/approve",
    tags=["Self-Service"],
    summary="Approve or reject a shift wish",
    description="Approve a shift wish. Requires Planer role.",
)
def approve_wish(
    wish_id: int, body: WishApprove, _cur_user: dict = Depends(require_planer)
):
    """Approve or reject a wish. On approval of WUNSCH with shift_id, the shift is
    written into the schedule."""
    db = get_db()
    # Load the wish
    wishes = db.get_wishes()
    wish = next((w for w in wishes if w.get("id") == wish_id), None)
    if wish is None:
        raise HTTPException(status_code=404, detail="Wunsch nicht gefunden")

    new_status = "approved" if body.action == "approve" else "rejected"

    # If approving a WUNSCH that has a shift_id, write to schedule
    if (
        body.action == "approve"
        and wish.get("wish_type") == "WUNSCH"
        and wish.get("shift_id")
    ):
        try:
            db.add_schedule_entry(
                employee_id=wish["employee_id"],
                date_str=wish["date"],
                shift_id=wish["shift_id"],
            )
        except ValueError:
            # Entry already exists — update instead
            db.delete_shift_only(employee_id=wish["employee_id"], date_str=wish["date"])
            db.add_schedule_entry(
                employee_id=wish["employee_id"],
                date_str=wish["date"],
                shift_id=wish["shift_id"],
            )

    # Update wish status
    updated = db.update_wish_status(wish_id, new_status)

    # Notify the employee
    action_label = "genehmigt" if body.action == "approve" else "abgelehnt"
    note_suffix = f" Hinweis: {body.note}" if body.note else ""
    create_notification(
        type="wish_decision",
        title=f"Schichtwunsch {action_label}",
        message=f"Dein Wunsch für den {wish['date']} wurde {action_label}.{note_suffix}",
        recipient_employee_id=wish.get("employee_id"),
        link="/self/wishes",
    )

    return updated


# ── Übergabe-Protokoll ────────────────────────────────────────────────────────
# In-memory store (reset on restart – kann später auf DB umgestellt werden)
import uuid as _uuid  # noqa: E402

_handover_notes: list[dict] = []


@router.get("/api/handover", tags=["Notes"], summary="List handover notes", description="Return handover notes for a specific date.")
def get_handover(date: str | None = None, shift_id: int | None = None, limit: int = 50):
    """Übergabe-Notizen abrufen, optional gefiltert nach Datum/Schicht."""
    notes = list(reversed(_handover_notes))  # neueste zuerst
    if date:
        notes = [n for n in notes if n["date"] == date]
    if shift_id is not None:
        notes = [n for n in notes if n.get("shift_id") == shift_id]
    return notes[:limit]


@router.post("/api/handover", tags=["Notes"], summary="Create handover note", description="Create a shift handover note for a date. Requires Planer role.")
def create_handover(body: dict, _cur_user: dict = Depends(require_planer)):
    """Neue Übergabe-Notiz anlegen."""
    note = {
        "id": str(_uuid.uuid4())[:8],
        "date": body.get("date", ""),
        "shift_id": body.get("shift_id"),
        "shift_name": body.get("shift_name", ""),
        "author": body.get("author", "Unbekannt"),
        "text": body.get("text", ""),
        "priority": body.get("priority", "normal"),  # normal | wichtig | kritisch
        "tags": body.get("tags", []),
        "created_at": body.get("created_at", ""),
        "resolved": False,
    }
    _handover_notes.append(note)
    return note


@router.patch("/api/handover/{note_id}", tags=["Notes"], summary="Update handover note")
def update_handover(
    note_id: str, body: dict, _cur_user: dict = Depends(require_planer)
):
    """Notiz aktualisieren (z.B. als erledigt markieren)."""
    for note in _handover_notes:
        if note["id"] == note_id:
            if "resolved" in body:
                note["resolved"] = body["resolved"]
            if "text" in body:
                note["text"] = body["text"]
            if "priority" in body:
                note["priority"] = body["priority"]
            return note
    from fastapi import HTTPException

    raise HTTPException(status_code=404, detail="Notiz nicht gefunden")


@router.delete(
    "/api/handover/{note_id}", tags=["Notes"], summary="Delete handover note",
    description="Übergabe-Notiz löschen.",
)
def delete_handover(note_id: str, _cur_user: dict = Depends(require_planer)):
    """Übergabe-Notiz löschen."""
    global _handover_notes
    before = len(_handover_notes)
    _handover_notes = [n for n in _handover_notes if n["id"] != note_id]
    if len(_handover_notes) == before:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Notiz nicht gefunden")
    return {"ok": True}


# ── Schicht-Tauschbörse ──────────────────────────────────────────


class SwapRequestCreate(BaseModel):
    requester_id: int = Field(..., gt=0)
    requester_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")  # YYYY-MM-DD
    partner_id: int = Field(..., gt=0)
    partner_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")  # YYYY-MM-DD
    note: str | None = Field("", max_length=500)


class SwapRequestResolve(BaseModel):
    action: str = Field(..., pattern=r"^(approve|reject)$")  # 'approve' | 'reject'
    resolved_by: str | None = Field("planner", max_length=100)
    reject_reason: str | None = Field("", max_length=500)


@router.get(
    "/api/swap-requests", tags=["Self-Service"], summary="List shift swap requests",
    description="Return shift swap requests, optionally filtered by status or employee.",
)
def list_swap_requests(
    status: str | None = None,
    employee_id: int | None = None,
    _cur_user: dict = Depends(require_auth),
):
    """List shift swap requests, optionally filtered by status or employee."""
    requests = get_db().get_swap_requests(status=status, employee_id=employee_id)
    # Enrich with employee names + shift info
    employees = {e["ID"]: e for e in get_db().get_employees(include_hidden=True)}
    shifts = {s["ID"]: s for s in get_db().get_shifts(include_hidden=True)}

    def get_shift_for(emp_id: int, date_str: str):
        sched = get_db().get_schedule_day(date_str)
        for entry in sched:
            if entry.get("employee_id") == emp_id:
                sid = entry.get("shift_id")
                if sid and sid in shifts:
                    s = shifts[sid]
                    return {
                        "id": sid,
                        "name": s.get("SHORTNAME", "?"),
                        "color": s.get("COLOR", "#888"),
                    }
        return None

    result = []
    for req in requests:
        r = dict(req)
        req_emp = employees.get(req["requester_id"], {})
        par_emp = employees.get(req["partner_id"], {})
        r["requester_name"] = (
            f"{req_emp.get('NAME', 'Gelöschter MA')}, {req_emp.get('FIRSTNAME', '')}"
            if req_emp
            else f"Gelöschter MA (ID {req['requester_id']})"
        )
        r["requester_short"] = (
            req_emp.get("SHORTNAME", f"#{req['requester_id']}")
            if req_emp
            else f"#{req['requester_id']}"
        )
        r["partner_name"] = (
            f"{par_emp.get('NAME', 'Gelöschter MA')}, {par_emp.get('FIRSTNAME', '')}"
            if par_emp
            else f"Gelöschter MA (ID {req['partner_id']})"
        )
        r["partner_short"] = (
            par_emp.get("SHORTNAME", f"#{req['partner_id']}")
            if par_emp
            else f"#{req['partner_id']}"
        )
        r["requester_shift"] = get_shift_for(req["requester_id"], req["requester_date"])
        r["partner_shift"] = get_shift_for(req["partner_id"], req["partner_date"])
        result.append(r)
    return result


@router.post(
    "/api/swap-requests", tags=["Self-Service"], summary="Create shift swap request",
    description="Create a new shift swap request between two employees. Requires Planer role.",
)
@limiter.limit("5/minute")
def create_swap_request(
    request: Request, body: SwapRequestCreate, _cur_user: dict = Depends(require_planer)
):
    """Create a new shift swap request."""
    from datetime import datetime as _dt4

    # Validate dates
    for d in [body.requester_date, body.partner_date]:
        try:
            _dt4.strptime(d, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Ungültiges Datum: {d}")
    if body.requester_id == body.partner_id:
        raise HTTPException(
            status_code=400, detail="Antragsteller und Partner müssen verschieden sein"
        )
    entry = get_db().create_swap_request(
        requester_id=body.requester_id,
        requester_date=body.requester_date,
        partner_id=body.partner_id,
        partner_date=body.partner_date,
        note=body.note or "",
    )
    get_db().log_action(
        "system",
        "CREATE",
        "swap_request",
        entry["id"],
        f"MA {body.requester_id} → MA {body.partner_id} ({body.requester_date}↔{body.partner_date})",
    )

    # ── Notification: inform the partner about incoming swap request ──
    try:
        employees = get_db().get_employees()
        requester = next(
            (e for e in employees if e.get("ID") == body.requester_id), None
        )
        req_name = (
            f"{requester.get('Vorname', '')} {requester.get('Nachname', '')}".strip()
            if requester
            else f"MA #{body.requester_id}"
        )
        create_notification(
            type="swap_request",
            title="🔄 Neue Tauschanfrage",
            message=f"{req_name} möchte den Dienst am {body.requester_date} mit dir tauschen (dein Datum: {body.partner_date}).",
            recipient_employee_id=body.partner_id,
            link="/tauschboerse",
        )
    except Exception:
        pass

    broadcast("swap_changed", {"action": "created", "swap_id": entry.get("id")})
    return entry


@router.patch(
    "/api/swap-requests/{swap_id}/resolve",
    tags=["Self-Service"],
    summary="Resolve shift swap request",
    description="Resolve (approve/reject) a shift swap request and execute the swap if approved. Requires Planer role.",
)
def resolve_swap_request(
    swap_id: int, body: SwapRequestResolve, _cur_user: dict = Depends(require_planer)
):
    """Approve or reject a swap request. If approved, executes the actual shift swap."""
    if body.action not in ("approve", "reject"):
        raise HTTPException(
            status_code=400, detail="action muss 'approve' oder 'reject' sein"
        )
    entry = get_db().resolve_swap_request(
        swap_id,
        body.action,
        resolved_by=body.resolved_by or "planner",
        reject_reason=body.reject_reason or "",
    )
    if entry is None:
        raise HTTPException(
            status_code=404, detail="Anfrage nicht gefunden oder bereits abgeschlossen"
        )

    if body.action == "approve":
        requester_id = entry["requester_id"]
        partner_id = entry["partner_id"]
        req_date = entry["requester_date"]
        par_date = entry["partner_date"]

        if req_date == par_date:
            # Same-date swap: use existing swap_shifts helper
            swap_result = swap_shifts(
                SwapShiftsRequest(
                    employee_id_1=requester_id,
                    employee_id_2=partner_id,
                    dates=[req_date],
                )
            )
        else:
            # Cross-date swap:
            # - requester (emp A) had shift on req_date → emp B gets it on req_date
            # - partner   (emp B) had shift on par_date → emp A gets it on par_date
            from sp5lib.dbf_reader import get_table_fields
            from sp5lib.dbf_writer import find_all_records

            db = get_db()
            errors: list = []

            def _collect(emp_id: int, date_str: str):
                result = []
                for table, kind in [("MASHI", "shift"), ("ABSEN", "absence")]:
                    filepath = db._table(table)
                    fields = get_table_fields(filepath)
                    matches = find_all_records(
                        filepath, fields, EMPLOYEEID=emp_id, DATE=date_str
                    )
                    for _, rec in matches:
                        if kind == "shift":
                            result.append(
                                {
                                    "kind": "shift",
                                    "shift_id": rec.get("SHIFTID"),
                                    "workplace_id": rec.get("WORKPLACID", 0),
                                }
                            )
                        else:
                            result.append(
                                {
                                    "kind": "absence",
                                    "leave_type_id": rec.get("LEAVETYPID"),
                                }
                            )
                return result

            def _write(emp_id: int, date_str: str, entries):
                for e in entries:
                    try:
                        if e["kind"] == "shift":
                            db.add_schedule_entry(emp_id, date_str, e["shift_id"])
                        elif e["kind"] == "absence" and e.get("leave_type_id"):
                            db.add_absence(emp_id, date_str, e["leave_type_id"])
                    except Exception as exc:
                        errors.append(f"MA {emp_id} / {date_str}: {exc}")

            entries_a_on_req = _collect(
                requester_id, req_date
            )  # A's shift on their date
            entries_b_on_par = _collect(partner_id, par_date)  # B's shift on their date

            # Delete originals
            db.delete_schedule_entry(requester_id, req_date)
            db.delete_schedule_entry(partner_id, par_date)

            # Cross-write: A gets B's shift on par_date, B gets A's shift on req_date
            _write(requester_id, par_date, entries_b_on_par)
            _write(partner_id, req_date, entries_a_on_req)

            swap_result = {
                "ok": True,
                "swapped_days": 1,
                "errors": errors,
                "message": f"Kreuz-Tausch: MA {requester_id} ({req_date}) ↔ MA {partner_id} ({par_date})"
                + (f", {len(errors)} Fehler" if errors else ""),
            }
        get_db().log_action(
            body.resolved_by or "planner",
            "UPDATE",
            "swap_request",
            swap_id,
            f"Genehmigt: MA {entry['requester_id']} ↔ MA {entry['partner_id']}",
        )
        # ── Notify both parties: approved ──
        try:
            for emp_id in [entry["requester_id"], entry["partner_id"]]:
                create_notification(
                    type="swap_status",
                    title="✅ Schichttausch genehmigt",
                    message=f"Der Tausch ({entry['requester_date']} ↔ {entry['partner_date']}) wurde genehmigt und im Schichtplan umgesetzt.",
                    recipient_employee_id=emp_id,
                    link="/tauschboerse",
                )
        except Exception:
            pass
        broadcast("swap_changed", {"action": "approved", "swap_id": swap_id})
        return {**entry, "swap_result": swap_result}

    get_db().log_action(
        body.resolved_by or "planner",
        "UPDATE",
        "swap_request",
        swap_id,
        f"Abgelehnt: {body.reject_reason}",
    )
    # ── Notify both parties: rejected ──
    try:
        reason_txt = f" Grund: {body.reject_reason}" if body.reject_reason else ""
        for emp_id in [entry["requester_id"], entry["partner_id"]]:
            create_notification(
                type="swap_status",
                title="❌ Schichttausch abgelehnt",
                message=f"Der Tausch ({entry['requester_date']} ↔ {entry['partner_date']}) wurde abgelehnt.{reason_txt}",
                recipient_employee_id=emp_id,
                link="/tauschboerse",
            )
    except Exception:
        pass
    broadcast("swap_changed", {"action": "rejected", "swap_id": swap_id})
    return entry


@router.delete(
    "/api/swap-requests/{swap_id}",
    tags=["Self-Service"],
    summary="Cancel shift swap request",
    description="Delete a swap request (cancel).",
)
def delete_swap_request(swap_id: int, _cur_user: dict = Depends(require_planer)):
    """Delete a swap request (cancel)."""
    deleted = get_db().delete_swap_request(swap_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    broadcast("swap_changed", {"action": "cancelled", "swap_id": swap_id})
    return {"ok": True}


# ─── Self-Service Swap Requests ─────────────────────────────


def _resolve_employee_for_user(cur_user: dict):
    """Find the employee record matching the logged-in user by name."""
    user_name = cur_user.get("NAME", "").strip().lower()
    db = get_db()
    employees = db.get_employees(include_hidden=False)
    employee = next(
        (e for e in employees if (e.get("NAME") or "").strip().lower() == user_name),
        None,
    )
    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Kein Mitarbeiter-Datensatz für diesen Benutzer gefunden",
        )
    return employee


class SelfSwapRequestCreate(BaseModel):
    partner_id: int = Field(..., gt=0)
    requester_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    partner_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    note: str | None = Field("", max_length=500)


@router.post(
    "/api/self/swap-requests",
    tags=["Self-Service"],
    summary="Employee creates own swap request",
    description="Self-service: employee creates a shift swap request with a partner.",
)
@limiter.limit("5/minute")
def create_self_swap_request(
    request: Request, body: SelfSwapRequestCreate, cur_user: dict = Depends(require_auth)
):
    """Employee offers a shift swap. Status starts as pending_partner until the partner accepts."""
    from datetime import datetime as _dt4

    employee = _resolve_employee_for_user(cur_user)
    requester_id = employee["ID"]

    for d in [body.requester_date, body.partner_date]:
        try:
            _dt4.strptime(d, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Ungültiges Datum: {d}")
    if requester_id == body.partner_id:
        raise HTTPException(
            status_code=400, detail="Du kannst nicht mit dir selbst tauschen"
        )

    entry = get_db().create_swap_request(
        requester_id=requester_id,
        requester_date=body.requester_date,
        partner_id=body.partner_id,
        partner_date=body.partner_date,
        note=body.note or "",
        status="pending_partner",
    )
    get_db().log_action(
        cur_user.get("NAME", "system"),
        "CREATE",
        "swap_request",
        entry["id"],
        f"Self-Service: MA {requester_id} → MA {body.partner_id} ({body.requester_date}↔{body.partner_date})",
    )

    # Notify partner
    try:
        req_name = f"{employee.get('Vorname', '')} {employee.get('Nachname', '')}".strip()
        if not req_name:
            req_name = employee.get("SHORTNAME", f"MA #{requester_id}")
        create_notification(
            type="swap_request",
            title="🔄 Neue Tauschanfrage",
            message=f"{req_name} möchte den Dienst am {body.requester_date} mit dir tauschen (dein Datum: {body.partner_date}). Bitte bestätige oder lehne ab.",
            recipient_employee_id=body.partner_id,
            link="/tauschboerse",
        )
    except Exception:
        pass

    return entry


class PartnerRespondBody(BaseModel):
    accept: bool


@router.patch(
    "/api/self/swap-requests/{swap_id}/respond",
    tags=["Self-Service"],
    summary="Partner accepts or declines a swap request",
    description="Self-service: partner accepts or declines a swap request.",
)
def partner_respond_swap(
    swap_id: int, body: PartnerRespondBody, cur_user: dict = Depends(require_auth)
):
    """The swap partner accepts or declines the swap before planner decides."""
    employee = _resolve_employee_for_user(cur_user)
    emp_id = employee["ID"]

    # Verify this employee is the partner
    db = get_db()
    all_reqs = db.get_swap_requests()
    req = next((r for r in all_reqs if r.get("id") == swap_id), None)
    if req is None:
        raise HTTPException(status_code=404, detail="Anfrage nicht gefunden")
    if req.get("partner_id") != emp_id:
        raise HTTPException(
            status_code=403, detail="Nur der Tauschpartner kann darauf antworten"
        )
    if req.get("status") != "pending_partner":
        raise HTTPException(
            status_code=400, detail="Anfrage wartet nicht auf Partner-Bestätigung"
        )

    result = db.partner_respond_swap(swap_id, body.accept)
    if result is None:
        raise HTTPException(status_code=400, detail="Konnte nicht verarbeitet werden")

    # Notify requester
    try:
        partner_name = f"{employee.get('Vorname', '')} {employee.get('Nachname', '')}".strip()
        if not partner_name:
            partner_name = employee.get("SHORTNAME", f"MA #{emp_id}")
        if body.accept:
            create_notification(
                type="swap_status",
                title="✅ Tauschpartner hat zugestimmt",
                message=f"{partner_name} hat deinen Tauschvorschlag ({req['requester_date']} ↔ {req['partner_date']}) angenommen. Warte auf Planer-Genehmigung.",
                recipient_employee_id=req["requester_id"],
                link="/tauschboerse",
            )
        else:
            create_notification(
                type="swap_status",
                title="❌ Tauschpartner hat abgelehnt",
                message=f"{partner_name} hat deinen Tauschvorschlag ({req['requester_date']} ↔ {req['partner_date']}) abgelehnt.",
                recipient_employee_id=req["requester_id"],
                link="/tauschboerse",
            )
    except Exception:
        pass

    db.log_action(
        cur_user.get("NAME", "system"),
        "UPDATE",
        "swap_request",
        swap_id,
        f"Partner {'akzeptiert' if body.accept else 'abgelehnt'}",
    )
    return result


@router.delete(
    "/api/self/swap-requests/{swap_id}",
    tags=["Self-Service"],
    summary="Employee cancels own swap request",
    description="Self-service: requester cancels their own pending swap request.",
)
def cancel_self_swap_request(
    swap_id: int, cur_user: dict = Depends(require_auth)
):
    """Employee can cancel their own pending swap request."""
    employee = _resolve_employee_for_user(cur_user)
    emp_id = employee["ID"]

    db = get_db()
    all_reqs = db.get_swap_requests()
    req = next((r for r in all_reqs if r.get("id") == swap_id), None)
    if req is None:
        raise HTTPException(status_code=404, detail="Anfrage nicht gefunden")
    if req.get("requester_id") != emp_id:
        raise HTTPException(
            status_code=403, detail="Nur der Antragsteller kann stornieren"
        )
    if req.get("status") not in ("pending_partner", "pending"):
        raise HTTPException(
            status_code=400, detail="Nur ausstehende Anfragen können storniert werden"
        )

    cancelled = db.cancel_swap_request(swap_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="Nicht gefunden")

    db.log_action(
        cur_user.get("NAME", "system"),
        "UPDATE",
        "swap_request",
        swap_id,
        "Selbst storniert",
    )
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# Self-Service routes (Leser role)
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/api/me/employee",
    tags=["Self-Service"],
    summary="Get current user's employee record",
    description="Returns the EMPL record matching the logged-in user by name, or null.",
)
def get_my_employee(cur_user: dict = Depends(require_auth)):
    """Returns the EMPL record matching the logged-in user by name, or null."""
    user_name = cur_user.get("NAME", "").strip().lower()
    db = get_db()
    employees = db.get_employees(include_hidden=False)
    match = next(
        (e for e in employees if (e.get("NAME") or "").strip().lower() == user_name),
        None,
    )
    return {"employee": match, "user_id": cur_user.get("ID")}


class SelfWishCreate(BaseModel):
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    wish_type: str = Field(..., pattern=r"^(?i:WUNSCH|SPERRUNG)$")  # WUNSCH | SPERRUNG (case-insensitive)
    shift_id: int | None = Field(None, gt=0)
    note: str | None = Field("", max_length=500)


@router.post("/api/self/wishes", tags=["Self-Service"], summary="Submit own wish/block", description="Leser can submit a Schichtwunsch or Sperrung for themselves.")
def create_self_wish(body: SelfWishCreate, cur_user: dict = Depends(require_auth)):
    """Leser can submit a Schichtwunsch or Sperrung for themselves."""
    user_name = cur_user.get("NAME", "").strip().lower()
    db = get_db()
    employees = db.get_employees(include_hidden=False)
    employee = next(
        (e for e in employees if (e.get("NAME") or "").strip().lower() == user_name),
        None,
    )
    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Kein Mitarbeiter-Datensatz für diesen Benutzer gefunden",
        )
    wish_type = body.wish_type.upper()
    if wish_type not in ("WUNSCH", "SPERRUNG"):
        raise HTTPException(
            status_code=400, detail="wish_type must be WUNSCH or SPERRUNG"
        )
    try:
        result = db.add_wish(
            employee_id=employee["ID"],
            date=body.date,
            wish_type=wish_type,
            shift_id=body.shift_id,
            note=body.note or "",
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return result


@router.delete(
    "/api/self/wishes/{wish_id}", tags=["Self-Service"], summary="Delete own wish",
    description="Leser can delete their own wishes.",
)
def delete_self_wish(wish_id: int, cur_user: dict = Depends(require_auth)):
    """Leser can delete their own wishes."""
    user_name = cur_user.get("NAME", "").strip().lower()
    db = get_db()
    employees = db.get_employees(include_hidden=False)
    employee = next(
        (e for e in employees if (e.get("NAME") or "").strip().lower() == user_name),
        None,
    )
    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Kein Mitarbeiter-Datensatz für diesen Benutzer gefunden",
        )
    # Verify the wish belongs to this employee
    wishes = db.get_wishes(employee_id=employee["ID"])
    wish = next((w for w in wishes if w.get("id") == wish_id), None)
    if wish is None:
        raise HTTPException(
            status_code=404, detail="Wunsch nicht gefunden oder gehört nicht dir"
        )
    deleted = db.delete_wish(wish_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Wunsch nicht gefunden")
    return {"deleted": wish_id}


class SelfAbsenceCreate(BaseModel):
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    leave_type_id: int = Field(..., gt=0)
    note: str | None = Field("", max_length=500)


@router.post(
    "/api/self/absences", tags=["Self-Service"], summary="Submit own absence request",
    description="Self-service: employee requests an absence (subject to approval).",
)
@limiter.limit("10/minute")
def create_self_absence(
    request: Request, body: SelfAbsenceCreate, cur_user: dict = Depends(require_auth)
):
    """Leser can submit an absence/vacation request for themselves."""
    user_name = cur_user.get("NAME", "").strip().lower()
    db = get_db()
    employees = db.get_employees(include_hidden=False)
    employee = next(
        (e for e in employees if (e.get("NAME") or "").strip().lower() == user_name),
        None,
    )
    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Kein Mitarbeiter-Datensatz für diesen Benutzer gefunden",
        )
    # Check if already exists
    existing = db.get_absences_list(employee_id=employee["ID"])
    if any(a.get("date") == body.date for a in existing):
        raise HTTPException(
            status_code=409, detail="Abwesenheit für dieses Datum bereits vorhanden"
        )
    result = db.add_absence(employee["ID"], body.date, body.leave_type_id)
    return result
