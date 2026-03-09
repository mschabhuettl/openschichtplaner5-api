"""iCal export router – .ics feed of employee shift schedules.

Provides two modes:
1. **Download** (authenticated): `/api/ical/my-schedule.ics` — one-time download
2. **Subscribe** (token-based): `/api/ical/feed/{token}.ics` — persistent URL for
   calendar subscriptions (Google Calendar, Apple Calendar, Outlook).
   No auth header needed — the URL-embedded token provides access.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

from ..dependencies import get_db, require_auth

router = APIRouter()

# Vienna timezone offset (CET=+1, CEST=+2) — we use UTC and let clients handle TZ
_TZ_VIENNA = timezone(timedelta(hours=1))


def _make_uid(employee_id: int, date_str: str, kind: str) -> str:
    """Generate a deterministic UID for an iCal event."""
    raw = f"{employee_id}-{date_str}-{kind}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16] + "@openschichtplaner5"


def _ical_dt(dt: datetime) -> str:
    """Format datetime as iCal DTSTART/DTEND value (UTC)."""
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _ical_date(d: date) -> str:
    """Format date as iCal all-day value."""
    return d.strftime("%Y%m%d")


def _escape_ical(text: str) -> str:
    """Escape special characters for iCal text fields."""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _parse_time(time_str: str) -> tuple[int, int] | None:
    """Parse 'HH:MM' or 'H:MM' to (hour, minute) tuple."""
    if not time_str or ":" not in time_str:
        return None
    try:
        parts = time_str.strip().split(":")
        return (int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        return None


def _build_ical(
    events: list[dict],
    cal_name: str,
) -> str:
    """Build a complete iCal string from a list of event dicts."""
    now = datetime.now(UTC)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//OpenSchichtplaner5//Schichtplan//DE",
        f"X-WR-CALNAME:{_escape_ical(cal_name)}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]

    for ev in events:
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{ev['uid']}")
        lines.append(f"DTSTAMP:{_ical_dt(now)}")

        if ev.get("all_day"):
            lines.append(f"DTSTART;VALUE=DATE:{ev['dtstart']}")
            # For all-day events, DTEND is the next day (exclusive)
            lines.append(f"DTEND;VALUE=DATE:{ev['dtend']}")
        else:
            lines.append(f"DTSTART:{ev['dtstart']}")
            lines.append(f"DTEND:{ev['dtend']}")

        lines.append(f"SUMMARY:{_escape_ical(ev['summary'])}")
        if ev.get("description"):
            lines.append(f"DESCRIPTION:{_escape_ical(ev['description'])}")
        if ev.get("categories"):
            lines.append(f"CATEGORIES:{_escape_ical(ev['categories'])}")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


@router.get(
    "/api/ical/my-schedule.ics",
    tags=["iCal"],
    summary="Download personal schedule as iCal",
    description=(
        "Returns the authenticated user's shift schedule for a given month "
        "as an iCal (.ics) file. Includes shifts and absences.\n\n"
        "Can be imported into Google Calendar, Apple Calendar, Outlook, etc."
    ),
    responses={
        200: {
            "description": "iCal file",
            "content": {"text/calendar": {}},
        },
    },
)
def get_my_ical(
    year: int = Query(..., description="Year (YYYY)"),
    month: int = Query(..., description="Month (1-12)"),
    user: dict = Depends(require_auth),
):
    """Export the current user's schedule as .ics file."""
    employee_id = user.get("EMPLOYEEID") or user.get("employee_id") or user.get("ID")
    if not employee_id:
        raise HTTPException(status_code=400, detail="Kein Mitarbeiter zugeordnet")

    return _generate_ical_response(employee_id, year, month)


@router.get(
    "/api/ical/schedule/{employee_id}.ics",
    tags=["iCal"],
    summary="Download employee schedule as iCal",
    description=(
        "Returns a specific employee's shift schedule for a given month "
        "as an iCal (.ics) file. Requires at least Planer role."
    ),
    responses={
        200: {
            "description": "iCal file",
            "content": {"text/calendar": {}},
        },
    },
)
def get_employee_ical(
    employee_id: int,
    year: int = Query(..., description="Year (YYYY)"),
    month: int = Query(..., description="Month (1-12)"),
    user: dict = Depends(require_auth),
):
    """Export a specific employee's schedule as .ics file.

    Any authenticated user can access their own schedule.
    Planer/Admin can access any employee's schedule.
    """
    own_id = user.get("EMPLOYEEID") or user.get("employee_id") or user.get("ID")
    if employee_id != own_id:
        from ..dependencies import _ROLE_LEVEL

        user_level = _ROLE_LEVEL.get(user.get("role", "Leser"), 1)
        if user_level < 2:  # Planer level
            raise HTTPException(
                status_code=403,
                detail="Nur Planer/Admin können fremde Schichtpläne exportieren",
            )

    return _generate_ical_response(employee_id, year, month)


def _generate_ical_response(
    employee_id: int, year: int, month: int
) -> Response:
    """Generate iCal response for an employee's monthly schedule."""
    if not (1 <= month <= 12):
        raise HTTPException(
            status_code=400, detail="Ungültiger Monat: muss zwischen 1 und 12 liegen"
        )
    if not (2000 <= year <= 2100):
        raise HTTPException(
            status_code=400,
            detail="Ungültiges Jahr: muss zwischen 2000 und 2100 liegen",
        )

    db = get_db()

    # Get employee name for calendar title
    employee = db.get_employee(employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail="Mitarbeiter nicht gefunden")

    emp_name = (
        f"{employee.get('FIRSTNAME', '')} {employee.get('LASTNAME', '')}".strip()
        or employee.get("NAME", f"MA-{employee_id}")
    )

    # Get schedule entries for this employee/month
    schedule = db.get_schedule(year=year, month=month)
    employee_entries = [
        e for e in schedule if e.get("employee_id") == employee_id
    ]

    # Get shift definitions for time lookups
    shifts = db.get_shifts(include_hidden=True)
    shifts_map = {s["ID"]: s for s in shifts}

    # Get leave types for absence names
    leave_types = db.get_leave_types()
    leave_map = {lt["ID"]: lt for lt in leave_types}

    # Build iCal events
    events: list[dict] = []

    for entry in employee_entries:
        date_str = entry.get("date", "")
        kind = entry.get("kind", "")

        if not date_str:
            continue

        try:
            entry_date = date.fromisoformat(date_str)
        except ValueError:
            continue

        weekday = entry_date.weekday()  # 0=Monday

        if kind in ("shift", "special_shift"):
            shift_id = entry.get("shift_id")
            shift = shifts_map.get(shift_id, {}) if shift_id else {}

            shift_name = (
                entry.get("custom_name")
                or shift.get("NAME", "")
                or entry.get("shift_name", "")
            )
            shift_short = (
                entry.get("custom_short")
                or shift.get("SHORTNAME", "")
                or entry.get("shift_short", "")
            )

            summary = shift_short or shift_name or "Schicht"
            description = shift_name if shift_name != summary else ""

            # Try to get start/end times from shift definition
            times_by_day = shift.get("TIMES_BY_WEEKDAY", {})
            day_times = times_by_day.get(weekday)

            if day_times and day_times.get("start") and day_times.get("end"):
                start_parsed = _parse_time(day_times["start"])
                end_parsed = _parse_time(day_times["end"])

                if start_parsed and end_parsed:
                    dt_start = datetime(
                        entry_date.year,
                        entry_date.month,
                        entry_date.day,
                        start_parsed[0],
                        start_parsed[1],
                        tzinfo=_TZ_VIENNA,
                    ).astimezone(UTC)

                    dt_end = datetime(
                        entry_date.year,
                        entry_date.month,
                        entry_date.day,
                        end_parsed[0],
                        end_parsed[1],
                        tzinfo=_TZ_VIENNA,
                    ).astimezone(UTC)

                    # Handle overnight shifts
                    if dt_end <= dt_start:
                        dt_end += timedelta(days=1)

                    events.append(
                        {
                            "uid": _make_uid(employee_id, date_str, f"shift-{shift_id}"),
                            "dtstart": _ical_dt(dt_start),
                            "dtend": _ical_dt(dt_end),
                            "summary": summary,
                            "description": description,
                            "categories": "Schicht",
                            "all_day": False,
                        }
                    )
                    continue

            # Fallback: all-day event if no times available
            next_day = entry_date + timedelta(days=1)
            events.append(
                {
                    "uid": _make_uid(employee_id, date_str, f"shift-{shift_id}"),
                    "dtstart": _ical_date(entry_date),
                    "dtend": _ical_date(next_day),
                    "summary": summary,
                    "description": description,
                    "categories": "Schicht",
                    "all_day": True,
                }
            )

        elif kind == "absence":
            leave_type_id = entry.get("leave_type_id")
            leave_type = leave_map.get(leave_type_id, {}) if leave_type_id else {}
            leave_name = (
                leave_type.get("NAME", "")
                or entry.get("leave_type_name", "")
                or "Abwesend"
            )

            next_day = entry_date + timedelta(days=1)
            events.append(
                {
                    "uid": _make_uid(employee_id, date_str, f"absence-{leave_type_id}"),
                    "dtstart": _ical_date(entry_date),
                    "dtend": _ical_date(next_day),
                    "summary": leave_name,
                    "description": "",
                    "categories": "Abwesenheit",
                    "all_day": True,
                }
            )

    # Build calendar
    month_name = date(year, month, 1).strftime("%B %Y")
    cal_name = f"Schichtplan {emp_name} – {month_name}"
    ical_str = _build_ical(events, cal_name)

    filename = f"schichtplan-{employee_id}-{year}-{month:02d}.ics"

    return Response(
        content=ical_str,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-cache",
        },
    )


# ── Subscribable iCal Feed ────────────────────────────────────────


def _generate_feed_ical(employee_id: int, months_back: int = 1, months_ahead: int = 3) -> str:
    """Generate a rolling iCal feed covering past and future months.

    Unlike the single-month download, the feed always includes a rolling
    window so subscribers see upcoming changes automatically.
    """
    db = get_db()

    employee = db.get_employee(employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail="Mitarbeiter nicht gefunden")

    emp_name = (
        f"{employee.get('FIRSTNAME', '')} {employee.get('LASTNAME', '')}".strip()
        or employee.get("NAME", f"MA-{employee_id}")
    )

    # Get shift definitions and leave types once
    shifts = db.get_shifts(include_hidden=True)
    shifts_map = {s["ID"]: s for s in shifts}
    leave_types = db.get_leave_types()
    leave_map = {lt["ID"]: lt for lt in leave_types}

    # Build rolling window of months
    today = date.today()
    events: list[dict] = []

    start_year, start_month = today.year, today.month - months_back
    if start_month < 1:
        start_year -= 1
        start_month += 12

    for offset in range(months_back + months_ahead + 1):
        m = start_month + offset
        y = start_year
        while m > 12:
            m -= 12
            y += 1

        schedule = db.get_schedule(year=y, month=m)
        employee_entries = [
            e for e in schedule if e.get("employee_id") == employee_id
        ]

        for entry in employee_entries:
            date_str = entry.get("date", "")
            kind = entry.get("kind", "")

            if not date_str:
                continue
            try:
                entry_date = date.fromisoformat(date_str)
            except ValueError:
                continue

            weekday = entry_date.weekday()

            if kind in ("shift", "special_shift"):
                shift_id = entry.get("shift_id")
                shift = shifts_map.get(shift_id, {}) if shift_id else {}
                shift_name = (
                    entry.get("custom_name")
                    or shift.get("NAME", "")
                    or entry.get("shift_name", "")
                )
                shift_short = (
                    entry.get("custom_short")
                    or shift.get("SHORTNAME", "")
                    or entry.get("shift_short", "")
                )
                summary = shift_short or shift_name or "Schicht"
                description = shift_name if shift_name != summary else ""

                # Try to get times
                times_by_day = shift.get("TIMES_BY_WEEKDAY", {})
                day_times = times_by_day.get(weekday)

                if day_times and day_times.get("start") and day_times.get("end"):
                    start_parsed = _parse_time(day_times["start"])
                    end_parsed = _parse_time(day_times["end"])
                    if start_parsed and end_parsed:
                        dt_start = datetime(
                            entry_date.year, entry_date.month, entry_date.day,
                            start_parsed[0], start_parsed[1], tzinfo=_TZ_VIENNA,
                        ).astimezone(UTC)
                        dt_end = datetime(
                            entry_date.year, entry_date.month, entry_date.day,
                            end_parsed[0], end_parsed[1], tzinfo=_TZ_VIENNA,
                        ).astimezone(UTC)
                        if dt_end <= dt_start:
                            dt_end += timedelta(days=1)
                        events.append({
                            "uid": _make_uid(employee_id, date_str, f"shift-{shift_id}"),
                            "dtstart": _ical_dt(dt_start),
                            "dtend": _ical_dt(dt_end),
                            "summary": summary,
                            "description": description,
                            "categories": "Schicht",
                            "all_day": False,
                        })
                        continue

                # Fallback: all-day
                next_day = entry_date + timedelta(days=1)
                events.append({
                    "uid": _make_uid(employee_id, date_str, f"shift-{shift_id}"),
                    "dtstart": _ical_date(entry_date),
                    "dtend": _ical_date(next_day),
                    "summary": summary,
                    "description": description,
                    "categories": "Schicht",
                    "all_day": True,
                })

            elif kind == "absence":
                leave_type_id = entry.get("leave_type_id")
                leave_type = leave_map.get(leave_type_id, {}) if leave_type_id else {}
                leave_name = (
                    leave_type.get("NAME", "")
                    or entry.get("leave_type_name", "")
                    or "Abwesend"
                )
                next_day = entry_date + timedelta(days=1)
                events.append({
                    "uid": _make_uid(employee_id, date_str, f"absence-{leave_type_id}"),
                    "dtstart": _ical_date(entry_date),
                    "dtend": _ical_date(next_day),
                    "summary": leave_name,
                    "description": "",
                    "categories": "Abwesenheit",
                    "all_day": True,
                })

    cal_name = f"Schichtplan {emp_name}"
    return _build_ical(events, cal_name)


@router.get(
    "/api/ical/feed/{token}.ics",
    tags=["iCal"],
    summary="Subscribable iCal feed (token-based, no auth required)",
    description=(
        "Returns a rolling iCal feed for the employee associated with the token.\n\n"
        "**No authentication header needed** — the URL-embedded token provides access.\n"
        "This URL can be pasted directly into Google Calendar, Apple Calendar, or Outlook\n"
        "as a calendar subscription. The calendar app will periodically re-fetch the URL\n"
        "to pick up schedule changes.\n\n"
        "The feed covers 1 month back and 3 months ahead (rolling window)."
    ),
    responses={
        200: {"description": "iCal feed", "content": {"text/calendar": {}}},
        404: {"description": "Invalid or revoked token"},
    },
)
def get_ical_feed(token: str):
    """Public iCal feed endpoint — token-based authentication."""
    db = get_db()
    employee_id = db.resolve_ical_token(token)
    if employee_id is None:
        raise HTTPException(
            status_code=404,
            detail="Ungültiger oder widerrufener Feed-Token",
        )

    ical_str = _generate_feed_ical(employee_id)

    return Response(
        content=ical_str,
        media_type="text/calendar; charset=utf-8",
        headers={
            # No Content-Disposition attachment — calendar apps expect inline
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


class IcalTokenResponse(BaseModel):
    token: str
    feed_url: str
    webcal_url: str


@router.post(
    "/api/ical/token",
    tags=["iCal"],
    summary="Generate or regenerate iCal feed token",
    description=(
        "Generates a new iCal feed token for the authenticated user.\n\n"
        "If the user already has a token, it is revoked and replaced.\n"
        "The old feed URL will stop working immediately.\n\n"
        "Returns the new token and ready-to-use feed URLs (http and webcal).\n\n"
        "**Required role:** any authenticated user"
    ),
    response_model=IcalTokenResponse,
)
def create_ical_token(request: Request, user: dict = Depends(require_auth)):
    """Generate a new iCal feed token for the current user."""
    employee_id = user.get("EMPLOYEEID") or user.get("employee_id") or user.get("ID")
    if not employee_id:
        raise HTTPException(status_code=400, detail="Kein Mitarbeiter zugeordnet")

    db = get_db()

    # Verify employee exists
    emp = db.get_employee(employee_id)
    if not emp:
        raise HTTPException(status_code=404, detail="Mitarbeiter nicht gefunden")

    token = db.create_ical_token(employee_id)

    # Build the feed URL from the request's base URL
    base = str(request.base_url).rstrip("/")
    feed_path = f"/api/ical/feed/{token}.ics"
    feed_url = f"{base}{feed_path}"
    webcal_url = feed_url.replace("http://", "webcal://").replace("https://", "webcal://")

    return IcalTokenResponse(
        token=token,
        feed_url=feed_url,
        webcal_url=webcal_url,
    )


@router.get(
    "/api/ical/token",
    tags=["iCal"],
    summary="Get current iCal feed token info",
    description=(
        "Returns the current iCal feed token and URLs for the authenticated user,\n"
        "or null/empty if no token exists.\n\n"
        "**Required role:** any authenticated user"
    ),
)
def get_ical_token(request: Request, user: dict = Depends(require_auth)):
    """Get the current iCal feed token for the current user."""
    employee_id = user.get("EMPLOYEEID") or user.get("employee_id") or user.get("ID")
    if not employee_id:
        raise HTTPException(status_code=400, detail="Kein Mitarbeiter zugeordnet")

    db = get_db()
    token = db.get_ical_token_for_employee(employee_id)

    if token is None:
        return {"token": None, "feed_url": None, "webcal_url": None}

    base = str(request.base_url).rstrip("/")
    feed_path = f"/api/ical/feed/{token}.ics"
    feed_url = f"{base}{feed_path}"
    webcal_url = feed_url.replace("http://", "webcal://").replace("https://", "webcal://")

    return {"token": token, "feed_url": feed_url, "webcal_url": webcal_url}


@router.delete(
    "/api/ical/token",
    tags=["iCal"],
    summary="Revoke iCal feed token",
    description=(
        "Revokes the current iCal feed token. The feed URL will stop working immediately.\n\n"
        "**Required role:** any authenticated user"
    ),
)
def revoke_ical_token(user: dict = Depends(require_auth)):
    """Revoke the current user's iCal feed token."""
    employee_id = user.get("EMPLOYEEID") or user.get("employee_id") or user.get("ID")
    if not employee_id:
        raise HTTPException(status_code=400, detail="Kein Mitarbeiter zugeordnet")

    db = get_db()
    revoked = db.revoke_ical_token(employee_id)

    if not revoked:
        return {"ok": True, "message": "Kein Token vorhanden"}

    return {"ok": True, "message": "Token widerrufen — Feed-URL ist ab sofort ungültig"}
