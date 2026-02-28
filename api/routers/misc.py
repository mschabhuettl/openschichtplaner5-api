"""Misc router: notes, wishes, handover, swap-requests, changelog, search, access."""
import re
import json
from fastapi import APIRouter, HTTPException, Query, Header, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
import re as _re
from ..dependencies import (
    get_db, require_admin, require_planer, require_auth, require_role,
    _sanitize_500, _logger, _sessions, get_current_user, limiter,
)

router = APIRouter()



# ── Notes ─────────────────────────────────────────────────────

@router.get("/api/notes", tags=["Notes"], summary="List notes", description="Return shift notes, optionally filtered by date or employee.")
def get_notes(
    date: Optional[str] = Query(None, description="Filter by date YYYY-MM-DD"),
    employee_id: Optional[int] = Query(None),
    year: Optional[int] = Query(None, description="Filter by year (use with month)"),
    month: Optional[int] = Query(None, description="Filter by month 1-12 (use with year)"),
):
    if year is not None and month is not None:
        import calendar as _cal
        last_day = _cal.monthrange(year, month)[1]
        date_from = f"{year:04d}-{month:02d}-01"
        date_to = f"{year:04d}-{month:02d}-{last_day:02d}"
        all_notes = get_db().get_notes(date=None, employee_id=employee_id)
        return [n for n in all_notes if date_from <= (n.get('date') or '') <= date_to]
    return get_db().get_notes(date=date, employee_id=employee_id)


class NoteCreate(BaseModel):
    date: str
    text: str
    employee_id: Optional[int] = 0
    text2: Optional[str] = ''
    category: Optional[str] = ''


@router.post("/api/notes", tags=["Notes"], summary="Add note", description="Create a new shift note. Requires Planer role.")
def add_note(body: NoteCreate, _cur_user: dict = Depends(require_planer)):
    try:
        from datetime import datetime
        datetime.strptime(body.date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")
    try:
        import html as _html
        result = get_db().add_note(
            date=body.date,
            text=_html.escape(body.text),
            employee_id=body.employee_id or 0,
            text2=_html.escape(body.text2 or ''),
            category=body.category or '',
        )
        return {"ok": True, "record": result}
    except Exception as e:
        raise _sanitize_500(e)


class NoteUpdate(BaseModel):
    text: Optional[str] = None
    text2: Optional[str] = None
    employee_id: Optional[int] = None
    date: Optional[str] = None
    category: Optional[str] = None


@router.put("/api/notes/{note_id}")
def update_note(note_id: int, body: NoteUpdate, _cur_user: dict = Depends(require_planer)):
    if body.date is not None:
        try:
            from datetime import datetime as _dt
            _dt.strptime(body.date, '%Y-%m-%d')
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format, use YYYY-MM-DD")
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
            raise HTTPException(status_code=404, detail="Note not found")
        return {"ok": True, "record": result}
    except HTTPException:
        raise
    except Exception as e:
        raise _sanitize_500(e)


@router.delete("/api/notes/{note_id}")
def delete_note(note_id: int, _cur_user: dict = Depends(require_planer)):
    try:
        count = get_db().delete_note(note_id)
        return {"ok": True, "deleted": count}
    except Exception as e:
        raise _sanitize_500(e)


@router.get("/api/search")
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
        t_bi = {t[i:i+2] for i in range(len(t) - 1)} if len(t) >= 2 else set()
        s_bi = {s[i:i+2] for i in range(len(s) - 1)} if len(s) >= 2 else set()
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
        short = emp.get('SHORTNAME', '') or ''
        number = emp.get('NUMBER', '') or ''
        score = max(
            _fuzzy_score(name, query),
            _fuzzy_score(short, query),
            _fuzzy_score(number, query) * 0.9,
        )
        if score > 0.25:
            results.append({
                "type": "employee",
                "id": emp.get("ID"),
                "title": name,
                "subtitle": f"Kürzel: {short}" if short else "",
                "path": "/employees",
                "icon": "👤",
                "score": score,
            })

    # ── Shifts ────────────────────────────────────────────────
    shifts = db.get_shifts(include_hidden=False)
    for sh in shifts:
        name = sh.get('NAME', '') or ''
        short = sh.get('SHORTNAME', '') or ''
        score = max(
            _fuzzy_score(name, query),
            _fuzzy_score(short, query) * 0.9,
        )
        if score > 0.25:
            results.append({
                "type": "shift",
                "id": sh.get("ID"),
                "title": name,
                "subtitle": f"Kürzel: {short}" if short else "",
                "path": "/shifts",
                "icon": "🕐",
                "score": score,
            })

    # ── Leave Types ───────────────────────────────────────────
    leave_types = db.get_leave_types(include_hidden=False)
    for lt in leave_types:
        name = lt.get('NAME', '') or ''
        short = lt.get('SHORTNAME', '') or ''
        score = max(
            _fuzzy_score(name, query),
            _fuzzy_score(short, query) * 0.9,
        )
        if score > 0.25:
            results.append({
                "type": "leave_type",
                "id": lt.get("ID"),
                "title": name,
                "subtitle": f"Kürzel: {short}" if short else "",
                "path": "/leave-types",
                "icon": "📋",
                "score": score,
            })

    # ── Groups ────────────────────────────────────────────────
    groups = db.get_groups(include_hidden=False)
    for grp in groups:
        name = grp.get('NAME', '') or ''
        short = grp.get('SHORTNAME', '') or ''
        score = max(
            _fuzzy_score(name, query),
            _fuzzy_score(short, query) * 0.9,
        )
        if score > 0.25:
            results.append({
                "type": "group",
                "id": grp.get("ID"),
                "title": name,
                "subtitle": f"Kürzel: {short}" if short else "",
                "path": "/groups",
                "icon": "🏢",
                "score": score,
            })

    # Sort by score descending, limit to 30 total
    results.sort(key=lambda x: -x["score"])
    results = results[:30]
    # Remove internal score field from output
    for r in results:
        del r["score"]

    return {"results": results, "query": query}


# ── Employee / Group Access Rights ───────────────────────────

class EmployeeAccessSet(BaseModel):
    user_id: int
    employee_id: int
    rights: int = 0


class GroupAccessSet(BaseModel):
    user_id: int
    group_id: int
    rights: int = 0


@router.get("/api/employee-access")
def get_employee_access(user_id: Optional[int] = Query(None), _cur_user: dict = Depends(require_admin)):
    """Get employee-level access restrictions."""
    return get_db().get_employee_access(user_id=user_id)


@router.post("/api/employee-access")
def set_employee_access(body: EmployeeAccessSet, _cur_user: dict = Depends(require_admin)):
    """Set employee-level access for a user."""
    try:
        result = get_db().set_employee_access(body.user_id, body.employee_id, body.rights)
        return {"ok": True, "record": result}
    except Exception as e:
        raise _sanitize_500(e)


@router.delete("/api/employee-access/{access_id}")
def delete_employee_access(access_id: int, _cur_user: dict = Depends(require_admin)):
    """Remove an employee access entry."""
    count = get_db().delete_employee_access(access_id)
    if count == 0:
        raise HTTPException(status_code=404, detail="Access record not found")
    return {"ok": True, "deleted": access_id}


@router.get("/api/group-access")
def get_group_access(user_id: Optional[int] = Query(None), _cur_user: dict = Depends(require_admin)):
    """Get group-level access restrictions."""
    return get_db().get_group_access(user_id=user_id)


@router.post("/api/group-access")
def set_group_access(body: GroupAccessSet, _cur_user: dict = Depends(require_admin)):
    """Set group-level access for a user."""
    try:
        result = get_db().set_group_access(body.user_id, body.group_id, body.rights)
        return {"ok": True, "record": result}
    except Exception as e:
        raise _sanitize_500(e)


@router.delete("/api/group-access/{access_id}")
def delete_group_access(access_id: int, _cur_user: dict = Depends(require_admin)):
    """Remove a group access entry."""
    count = get_db().delete_group_access(access_id)
    if count == 0:
        raise HTTPException(status_code=404, detail="Access record not found")
    return {"ok": True, "deleted": access_id}


# ── Changelog / Aktivitätsprotokoll ─────────────────────────

@router.get("/api/changelog")
def get_changelog(
    limit: int = Query(100, description="Max entries to return"),
    user: Optional[str] = Query(None, description="Filter by user"),
    date_from: Optional[str] = Query(None, description="ISO date YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="ISO date YYYY-MM-DD"),
):
    """Return activity log entries from changelog.json."""
    return get_db().get_changelog(limit=limit, user=user, date_from=date_from, date_to=date_to)


class ChangelogEntry(BaseModel):
    user: str
    action: str        # CREATE / UPDATE / DELETE
    entity: str        # employee / shift / schedule / ...
    entity_id: int
    details: Optional[str] = ""


@router.post("/api/changelog")
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

@router.get("/api/wishes")
def get_wishes(
    employee_id: Optional[int] = None,
    year: Optional[int] = None,
    month: Optional[int] = None,
):
    return get_db().get_wishes(employee_id=employee_id, year=year, month=month)


class WishCreate(BaseModel):
    employee_id: int
    date: str
    wish_type: str  # WUNSCH | SPERRUNG
    shift_id: Optional[int] = None
    note: Optional[str] = ''


@router.post("/api/wishes")
def create_wish(body: WishCreate, _cur_user: dict = Depends(require_planer)):
    wish_type = body.wish_type.upper()
    if wish_type not in ('WUNSCH', 'SPERRUNG'):
        raise HTTPException(status_code=400, detail="wish_type must be WUNSCH or SPERRUNG")
    return get_db().add_wish(
        employee_id=body.employee_id,
        date=body.date,
        wish_type=wish_type,
        shift_id=body.shift_id,
        note=body.note or '',
    )


@router.delete("/api/wishes/{wish_id}")
def delete_wish(wish_id: int, _cur_user: dict = Depends(require_planer)):
    deleted = get_db().delete_wish(wish_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Wish not found")
    return {"deleted": wish_id}


# ── Übergabe-Protokoll ────────────────────────────────────────────────────────
# In-memory store (reset on restart – kann später auf DB umgestellt werden)
import uuid as _uuid

_handover_notes: list[dict] = []

@router.get("/api/handover")
def get_handover(date: str | None = None, shift_id: int | None = None, limit: int = 50):
    """Übergabe-Notizen abrufen, optional gefiltert nach Datum/Schicht."""
    notes = list(reversed(_handover_notes))  # neueste zuerst
    if date:
        notes = [n for n in notes if n["date"] == date]
    if shift_id is not None:
        notes = [n for n in notes if n.get("shift_id") == shift_id]
    return notes[:limit]

@router.post("/api/handover")
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

@router.patch("/api/handover/{note_id}")
def update_handover(note_id: str, body: dict, _cur_user: dict = Depends(require_planer)):
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

@router.delete("/api/handover/{note_id}")
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
    requester_id: int
    requester_date: str   # YYYY-MM-DD
    partner_id: int
    partner_date: str     # YYYY-MM-DD
    note: Optional[str] = ''


class SwapRequestResolve(BaseModel):
    action: str           # 'approve' | 'reject'
    resolved_by: Optional[str] = 'planner'
    reject_reason: Optional[str] = ''


@router.get("/api/swap-requests")
def list_swap_requests(
    status: Optional[str] = None,
    employee_id: Optional[int] = None,
    _cur_user: dict = Depends(require_auth),
):
    """List shift swap requests, optionally filtered by status or employee."""
    requests = get_db().get_swap_requests(status=status, employee_id=employee_id)
    # Enrich with employee names + shift info
    employees = {e['ID']: e for e in get_db().get_employees(include_hidden=True)}
    shifts = {s['ID']: s for s in get_db().get_shifts(include_hidden=True)}

    def get_shift_for(emp_id: int, date_str: str):
        sched = get_db().get_schedule_day(date_str)
        for entry in sched:
            if entry.get('employee_id') == emp_id:
                sid = entry.get('shift_id')
                if sid and sid in shifts:
                    s = shifts[sid]
                    return {'id': sid, 'name': s.get('SHORTNAME', '?'), 'color': s.get('COLOR', '#888')}
        return None

    result = []
    for req in requests:
        r = dict(req)
        req_emp = employees.get(req['requester_id'], {})
        par_emp = employees.get(req['partner_id'], {})
        r['requester_name'] = f"{req_emp.get('NAME', 'Gelöschter MA')}, {req_emp.get('FIRSTNAME', '')}" if req_emp else f"Gelöschter MA (ID {req['requester_id']})"
        r['requester_short'] = req_emp.get('SHORTNAME', f"#{req['requester_id']}") if req_emp else f"#{req['requester_id']}"
        r['partner_name'] = f"{par_emp.get('NAME', 'Gelöschter MA')}, {par_emp.get('FIRSTNAME', '')}" if par_emp else f"Gelöschter MA (ID {req['partner_id']})"
        r['partner_short'] = par_emp.get('SHORTNAME', f"#{req['partner_id']}") if par_emp else f"#{req['partner_id']}"
        r['requester_shift'] = get_shift_for(req['requester_id'], req['requester_date'])
        r['partner_shift'] = get_shift_for(req['partner_id'], req['partner_date'])
        result.append(r)
    return result


@router.post("/api/swap-requests")
@limiter.limit("5/minute")
def create_swap_request(request: Request, body: SwapRequestCreate, _cur_user: dict = Depends(require_planer)):
    """Create a new shift swap request."""
    from datetime import datetime as _dt4
    # Validate dates
    for d in [body.requester_date, body.partner_date]:
        try:
            _dt4.strptime(d, '%Y-%m-%d')
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Ungültiges Datum: {d}")
    if body.requester_id == body.partner_id:
        raise HTTPException(status_code=400, detail="Antragsteller und Partner müssen verschieden sein")
    entry = get_db().create_swap_request(
        requester_id=body.requester_id,
        requester_date=body.requester_date,
        partner_id=body.partner_id,
        partner_date=body.partner_date,
        note=body.note or '',
    )
    get_db().log_action('system', 'CREATE', 'swap_request', entry['id'],
                        f"MA {body.requester_id} → MA {body.partner_id} ({body.requester_date}↔{body.partner_date})")
    return entry


@router.patch("/api/swap-requests/{swap_id}/resolve")
def resolve_swap_request(swap_id: int, body: SwapRequestResolve, _cur_user: dict = Depends(require_planer)):
    """Approve or reject a swap request. If approved, executes the actual shift swap."""
    if body.action not in ('approve', 'reject'):
        raise HTTPException(status_code=400, detail="action muss 'approve' oder 'reject' sein")
    entry = get_db().resolve_swap_request(swap_id, body.action,
                                          resolved_by=body.resolved_by or 'planner',
                                          reject_reason=body.reject_reason or '')
    if entry is None:
        raise HTTPException(status_code=404, detail="Anfrage nicht gefunden oder bereits abgeschlossen")

    if body.action == 'approve':
        # Execute the actual shift swap for both dates
        swap_result = swap_shifts(SwapShiftsRequest(
            employee_id_1=entry['requester_id'],
            employee_id_2=entry['partner_id'],
            dates=[entry['requester_date']] if entry['requester_date'] == entry['partner_date']
                  else [entry['requester_date'], entry['partner_date']],
        ))
        # If different dates, we need to swap requester→partner_date and partner→requester_date
        if entry['requester_date'] != entry['partner_date']:
            # Custom cross-date swap: move requester's shift to partner_date and vice versa
            pass  # The swap above handles same-dates; cross-date swap is complex — mark as todo
        get_db().log_action(body.resolved_by or 'planner', 'UPDATE', 'swap_request', swap_id,
                            f"Genehmigt: MA {entry['requester_id']} ↔ MA {entry['partner_id']}")
        return {**entry, 'swap_result': swap_result}

    get_db().log_action(body.resolved_by or 'planner', 'UPDATE', 'swap_request', swap_id,
                        f"Abgelehnt: {body.reject_reason}")
    return entry


@router.delete("/api/swap-requests/{swap_id}")
def delete_swap_request(swap_id: int, _cur_user: dict = Depends(require_planer)):
    """Delete a swap request (cancel)."""
    deleted = get_db().delete_swap_request(swap_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    return {"ok": True}
