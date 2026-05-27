"""ORM-mirror admin router.

Materializes a read-only SQLAlchemy projection of the DBF master-data
definition tables (shifts / leave types / workplaces) via
libopenschichtplaner5's sync utilities and exposes it through the 1.2.0
repositories. This is the gradual DBF→ORM migration path the library is built
for: the DBF files stay the source of truth, while the ORM store is a
queryable, backend-agnostic mirror that works identically on SQLite and
PostgreSQL.

All endpoints are admin-only and additive — nothing here touches the live DBF
read/write flows. The mirror lives in its own ``sp5_orm.db`` next to the DBF
data directory (the same store the Companies router uses).
"""

import os

from fastapi import APIRouter, Depends

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
    """Refresh the master-data ORM mirror from the live DBF files.

    Upserts the three foreign-key-free definition tables — shifts, leave types
    and workplaces — and returns the per-table row counts. Safe to call
    repeatedly; the library's sync uses upsert semantics keyed by DBF ID.
    """
    from sp5lib.orm.base import get_session
    from sp5lib.orm.sync import sync_leave_types, sync_shifts, sync_workplaces

    daten = _daten_path()
    session = get_session(_get_orm_engine())
    try:
        stats = {
            "shifts": sync_shifts(session, daten),
            "leave_types": sync_leave_types(session, daten),
            "workplaces": sync_workplaces(session, daten),
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
