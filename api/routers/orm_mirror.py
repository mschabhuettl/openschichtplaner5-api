"""ORM-mirror admin router.

Materializes a read-only SQLAlchemy projection of the DBF data via
libopenschichtplaner5's sync utilities and exposes it through the library's
repositories. Two layers are mirrored:

* master-data definitions — shifts / leave types / workplaces (lib 1.2.0), and
* schedule entries — shift assignments (5MASHI), special shifts (5SPSHI) and
  absences (5ABSEN) with date-range queries (lib 1.3.0).

This is the gradual DBF→ORM migration path the library is built for: the DBF
files stay the source of truth, while the ORM store is a queryable,
backend-agnostic mirror that works identically on SQLite and PostgreSQL.

All endpoints are admin-only and additive — nothing here touches the live DBF
read/write flows. The mirror lives in its own ``sp5_orm.db`` next to the DBF
data directory (the same store the Companies router uses).
"""

import os

from fastapi import APIRouter, Depends, Query

from ..dependencies import _sanitize_500, require_admin

router = APIRouter(prefix="/api/admin/orm", tags=["ORM Mirror"])


# ── Helpers ──────────────────────────────────────────────────


def _get_orm_engine():
    """Create (and migrate) the SQLite engine for the ORM mirror DB.

    The mirror DB sits alongside the DBF data directory, matching the location
    the Companies router uses so both share a single ORM store.
    """
    from sp5lib.orm import get_engine, init_db

    import api.main as _main

    orm_db = os.path.join(os.path.dirname(_main.DB_PATH), "sp5_orm.db")
    engine = get_engine(f"sqlite:///{orm_db}")
    init_db(engine)
    return engine


def _get_orm_session():
    """Return an ORM session bound to the mirror DB."""
    from sp5lib.orm.base import get_session

    return get_session(_get_orm_engine())


def _daten_path() -> str:
    """Absolute path to the live DBF data directory (the sync source)."""
    import api.main as _main

    return _main.DB_PATH


# ── Endpoints ────────────────────────────────────────────────


@router.post("/sync")
def sync_orm_mirror(user: dict = Depends(require_admin)):
    """Refresh the ORM mirror from the live DBF files.

    Upserts the master-data definition tables (shifts, leave types, workplaces)
    and the schedule-entry tables (shift assignments, special shifts, absences)
    and returns the per-table row counts. Safe to call repeatedly; the library's
    sync uses upsert semantics keyed by DBF ID, and rows with invalid dates are
    skipped.

    Note: ``sync_all`` is intentionally not used — it also syncs
    ``group_assignments`` (5GRASG.DBF), whose DBF IDs are not unique and trip a
    UNIQUE constraint on dirty data (reported to the lib). The tables mirrored
    here have no such issue.
    """
    from sp5lib.orm.base import get_session
    from sp5lib.orm.sync import (
        sync_absences,
        sync_leave_types,
        sync_shift_assignments,
        sync_shifts,
        sync_special_shifts,
        sync_workplaces,
    )

    daten = _daten_path()
    session = get_session(_get_orm_engine())
    try:
        stats = {
            "shifts": sync_shifts(session, daten),
            "leave_types": sync_leave_types(session, daten),
            "workplaces": sync_workplaces(session, daten),
            "shift_assignments": sync_shift_assignments(session, daten),
            "special_shifts": sync_special_shifts(session, daten),
            "absences": sync_absences(session, daten),
        }
        session.commit()
        return {"ok": True, "synced": stats}
    except Exception as e:
        session.rollback()
        raise _sanitize_500(e, "sync_orm_mirror")
    finally:
        session.close()


@router.get("/shifts")
def list_orm_shifts(include_hidden: bool = False, user: dict = Depends(require_admin)):
    """List shift definitions from the ORM mirror (DBF-shaped dicts)."""
    from sp5lib.orm.repository import ShiftRepository

    session = _get_orm_session()
    try:
        return [s.to_dict() for s in ShiftRepository(session).list(include_hidden=include_hidden)]
    except Exception as e:
        raise _sanitize_500(e, "list_orm_shifts")
    finally:
        session.close()


@router.get("/leave-types")
def list_orm_leave_types(include_hidden: bool = False, user: dict = Depends(require_admin)):
    """List leave/absence types from the ORM mirror (DBF-shaped dicts)."""
    from sp5lib.orm.repository import LeaveTypeRepository

    session = _get_orm_session()
    try:
        return [
            lt.to_dict() for lt in LeaveTypeRepository(session).list(include_hidden=include_hidden)
        ]
    except Exception as e:
        raise _sanitize_500(e, "list_orm_leave_types")
    finally:
        session.close()


@router.get("/workplaces")
def list_orm_workplaces(include_hidden: bool = False, user: dict = Depends(require_admin)):
    """List workplace definitions from the ORM mirror (DBF-shaped dicts)."""
    from sp5lib.orm.repository import WorkplaceRepository

    session = _get_orm_session()
    try:
        return [
            w.to_dict() for w in WorkplaceRepository(session).list(include_hidden=include_hidden)
        ]
    except Exception as e:
        raise _sanitize_500(e, "list_orm_workplaces")
    finally:
        session.close()


# ── Schedule entries (lib 1.3.0) — date-range queryable ──────────


@router.get("/shift-assignments")
def list_orm_shift_assignments(
    date_from: str | None = Query(None, description="ISO date (inclusive lower bound)"),
    date_to: str | None = Query(None, description="ISO date (inclusive upper bound)"),
    employee_id: int | None = Query(None, description="Filter by employee ID"),
    user: dict = Depends(require_admin),
):
    """List regular schedule entries (5MASHI) from the ORM mirror, filterable by
    date range and/or employee."""
    from sp5lib.orm.repository import ShiftAssignmentRepository

    session = _get_orm_session()
    try:
        rows = ShiftAssignmentRepository(session).list(
            date_from=date_from, date_to=date_to, employee_id=employee_id
        )
        return [r.to_dict() for r in rows]
    except Exception as e:
        raise _sanitize_500(e, "list_orm_shift_assignments")
    finally:
        session.close()


@router.get("/special-shifts")
def list_orm_special_shifts(
    date_from: str | None = Query(None, description="ISO date (inclusive lower bound)"),
    date_to: str | None = Query(None, description="ISO date (inclusive upper bound)"),
    employee_id: int | None = Query(None, description="Filter by employee ID"),
    user: dict = Depends(require_admin),
):
    """List special / one-off shifts (5SPSHI) from the ORM mirror, filterable by
    date range and/or employee."""
    from sp5lib.orm.repository import SpecialShiftRepository

    session = _get_orm_session()
    try:
        rows = SpecialShiftRepository(session).list(
            date_from=date_from, date_to=date_to, employee_id=employee_id
        )
        return [r.to_dict() for r in rows]
    except Exception as e:
        raise _sanitize_500(e, "list_orm_special_shifts")
    finally:
        session.close()


@router.get("/absences")
def list_orm_absences(
    date_from: str | None = Query(None, description="ISO date (inclusive lower bound)"),
    date_to: str | None = Query(None, description="ISO date (inclusive upper bound)"),
    employee_id: int | None = Query(None, description="Filter by employee ID"),
    user: dict = Depends(require_admin),
):
    """List absences / leave entries (5ABSEN) from the ORM mirror, filterable by
    date range and/or employee."""
    from sp5lib.orm.repository import AbsenceRepository

    session = _get_orm_session()
    try:
        rows = AbsenceRepository(session).list(
            date_from=date_from, date_to=date_to, employee_id=employee_id
        )
        return [r.to_dict() for r in rows]
    except Exception as e:
        raise _sanitize_500(e, "list_orm_absences")
    finally:
        session.close()
