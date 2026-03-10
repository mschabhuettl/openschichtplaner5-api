"""Employee availability management router.

Allows employees to define their weekly availability by day of week (0=Monday .. 6=Sunday),
each with a list of time windows. Data is stored in a JSON file.
"""

import json
import os
import threading
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from ..dependencies import _logger, get_db

router = APIRouter()

# ── Storage ───────────────────────────────────────────────────────────────────

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
_AVAILABILITY_FILE = os.path.join(_DATA_DIR, "availability.json")
_LOCK = threading.Lock()


def _read_all() -> dict:
    """Read all availability data. Returns dict keyed by employee_id (as string)."""
    try:
        with open(_AVAILABILITY_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_all(data: dict) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_AVAILABILITY_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── Schemas ───────────────────────────────────────────────────────────────────

_TIME_PATTERN = r"^\d{2}:\d{2}$"


class TimeWindow(BaseModel):
    """A single availability time window within a day."""

    start: str = Field(..., pattern=_TIME_PATTERN, description="Start time HH:MM")
    end: str = Field(..., pattern=_TIME_PATTERN, description="End time HH:MM")

    @field_validator("start", "end")
    @classmethod
    def validate_time(cls, v: str) -> str:
        parts = v.split(":")
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError(f"Invalid time: {v}")
        return v

    @model_validator(mode="after")
    def end_after_start(self) -> "TimeWindow":
        if self.start >= self.end:
            raise ValueError(
                f"Endzeit ({self.end}) muss nach Startzeit ({self.start}) liegen"
            )
        return self


class DayAvailability(BaseModel):
    """Availability for a single day of the week."""

    day: int = Field(..., ge=0, le=6, description="Wochentag: 0=Montag, 6=Sonntag")
    available: bool = Field(True, description="Whether the employee is available on this day")
    time_windows: list[TimeWindow] = Field(
        default_factory=list,
        description="List of time slots (empty = all day when available=true)",
    )

    @model_validator(mode="after")
    def validate_windows(self) -> "DayAvailability":
        if not self.available and self.time_windows:
            raise ValueError(
                f"Tag {self.day}: Zeitfenster nicht erlaubt wenn available=false"
            )
        # Check for overlapping windows
        windows = sorted(self.time_windows, key=lambda w: w.start)
        for i in range(1, len(windows)):
            if windows[i].start < windows[i - 1].end:
                raise ValueError(
                    f"Day {self.day}: time slots overlap "
                    f"({windows[i-1].start}-{windows[i-1].end} and {windows[i].start}-{windows[i].end})"
                )
        return self


class AvailabilityUpdate(BaseModel):
    """Full weekly availability update for an employee."""

    days: list[DayAvailability] = Field(
        ...,
        min_length=1,
        max_length=7,
        description="Availability per weekday",
    )

    @field_validator("days")
    @classmethod
    def unique_days(cls, v: list[DayAvailability]) -> list[DayAvailability]:
        seen = set()
        for d in v:
            if d.day in seen:
                raise ValueError(f"Wochentag {d.day} doppelt angegeben")
            seen.add(d.day)
        return v


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get(
    "/api/employees/{emp_id}/availability",
    tags=["Availability"],
    summary="Get employee availability",
    description="Returns the weekly availability for an employee. "
    "Each day (0=Monday .. 6=Sunday) can have multiple time windows.",
)
def get_availability(emp_id: int):
    # Verify employee exists
    emp = get_db().get_employee(emp_id)
    if emp is None:
        raise HTTPException(
            status_code=404, detail=f"Mitarbeiter ID {emp_id} nicht gefunden"
        )

    with _LOCK:
        data = _read_all()

    emp_data = data.get(str(emp_id))
    if emp_data is None:
        # Return default: all days available, no specific windows
        return {
            "employee_id": emp_id,
            "days": [
                {"day": d, "available": True, "time_windows": []} for d in range(7)
            ],
            "updated_at": None,
        }
    return emp_data


@router.post(
    "/api/employees/{emp_id}/availability",
    tags=["Availability"],
    summary="Set employee availability",
    description="Set the full weekly availability for an employee. "
    "Replaces any existing availability data.",
)
def set_availability(emp_id: int, body: AvailabilityUpdate):
    emp = get_db().get_employee(emp_id)
    if emp is None:
        raise HTTPException(
            status_code=404, detail=f"Mitarbeiter ID {emp_id} nicht gefunden"
        )

    record = {
        "employee_id": emp_id,
        "days": [d.model_dump() for d in body.days],
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }

    with _LOCK:
        data = _read_all()
        data[str(emp_id)] = record
        _write_all(data)

    _logger.info("AVAILABILITY SET | emp_id=%d days=%d", emp_id, len(body.days))
    return {"ok": True, "availability": record}


@router.put(
    "/api/employees/{emp_id}/availability",
    tags=["Availability"],
    summary="Update employee availability",
    description="Partial update: only the days included in the request are updated. "
    "Other days remain unchanged.",
)
def update_availability(emp_id: int, body: AvailabilityUpdate):
    emp = get_db().get_employee(emp_id)
    if emp is None:
        raise HTTPException(
            status_code=404, detail=f"Mitarbeiter ID {emp_id} nicht gefunden"
        )

    with _LOCK:
        data = _read_all()
        existing = data.get(str(emp_id))

        if existing is None:
            # No existing data — treat as full set
            existing_days = {
                d: {"day": d, "available": True, "time_windows": []} for d in range(7)
            }
        else:
            existing_days = {d["day"]: d for d in existing.get("days", [])}

        # Merge: update only the provided days
        for d in body.days:
            existing_days[d.day] = d.model_dump()

        record = {
            "employee_id": emp_id,
            "days": [existing_days[k] for k in sorted(existing_days.keys())],
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        data[str(emp_id)] = record
        _write_all(data)

    _logger.info(
        "AVAILABILITY UPDATE | emp_id=%d updated_days=%s",
        emp_id,
        [d.day for d in body.days],
    )
    return {"ok": True, "availability": record}
