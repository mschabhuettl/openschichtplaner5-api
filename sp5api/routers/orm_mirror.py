"""ORM-mirror admin router.

Materializes a read-only SQLAlchemy projection of the DBF data via
libopenschichtplaner5's sync utilities and exposes it through the library's
repositories. ``POST /sync`` mirrors all 19 supported tables via the library's
``sync_all``; the read endpoints cover:

* master-data definitions — shifts / leave types / workplaces (lib 1.2.0),
* schedule entries — shift assignments (5MASHI), special shifts (5SPSHI) and
  absences (5ABSEN) with date-range queries (lib 1.3.0),
* calendar data — holidays (5HOLID) and periods (5PERIO) (lib 1.4.0),
* time accounting — bookings (5BOOK), overtime (5OVER) and leave
  entitlements (5LEAEN) (lib 1.5.0), and
* planning data — shift/special demand (5SHDEM/5SPDEM), cycles and cycle
  assignments (5CYCLE/5CYASS) and restrictions (5RESTR) (lib 1.6.0). With this
  the read mirror covers the full DBF schema (19 tables).

This is the gradual DBF→ORM migration path the library is built for: the DBF
files stay the source of truth, while the ORM store is a queryable,
backend-agnostic mirror that works identically on SQLite and PostgreSQL.

All endpoints are admin-only and additive — nothing here touches the live DBF
read/write flows. The mirror lives in its own ``sp5_orm.db`` next to the DBF
data directory (the same store the Companies router uses).
"""

import os

from fastapi import APIRouter, Depends, Query

from .._paths import state_path
from ..dependencies import _sanitize_500, require_admin

router = APIRouter(prefix="/api/admin/orm", tags=["ORM Mirror"])


# ── Helpers ──────────────────────────────────────────────────


def _get_orm_engine():
    """Erzeugt (und migriert) die SQLite-Engine der ORM-Spiegel-DB.

    Die Spiegel-DB liegt im konsolidierten, beschreibbaren State-Verzeichnis
    (``state_path``/``SP5_STATE_DIR``), passend zum Companies-Router — beide
    teilen EINEN ORM-Store. Sie darf NICHT neben den DBF-Daten liegen: die sind
    oft read-only gemountet, und ``dirname(SP5_DB_PATH)`` ist das Eltern-
    Verzeichnis des Datenordners (im Container ``/app``, root-eigen) —
    ``init_db`` scheiterte dort mit EACCES.
    """
    from sp5lib.orm import get_engine, init_db

    orm_db = state_path("sp5_orm.db")
    engine = get_engine(f"sqlite:///{orm_db}")
    init_db(engine)
    return engine


def _get_orm_session():
    """Liefert eine an die Spiegel-DB gebundene ORM-Session."""
    from sp5lib.orm.base import get_session

    return get_session(_get_orm_engine())


def _daten_path() -> str:
    """Absoluter Pfad zum Live-DBF-Datenverzeichnis (die Sync-Quelle)."""
    import sp5api.main as _main

    return _main.DB_PATH


# ── Endpoints ────────────────────────────────────────────────


@router.post("/sync")
def sync_orm_mirror(user: dict = Depends(require_admin)):
    """Frischt den ORM-Spiegel aus den Live-DBF-Dateien auf.

    Delegiert an ``sync_all`` der Bibliothek, das alle 19 unterstützten
    Tabellen spiegelt (Stammdaten, Planeinträge, Kalender, Zeitkonten- und
    Planungsdaten) und die Zeilenzahlen je Tabelle liefert. Beliebig oft
    aufrufbar: der Sync nutzt Upsert-Semantik, Zeilen mit ungültigen Daten
    werden übersprungen, und seit lib 1.4.0 dedupliziert
    ``sync_group_assignments`` und überspringt hängende Zeilen — ``sync_all``
    läuft also auch auf schmutzigen DBF-Daten sauber durch. ``sync_all``
    öffnet, committet und schließt seine Session selbst.
    """
    from sp5lib.orm.sync import sync_all

    try:
        stats = sync_all(_get_orm_engine(), _daten_path())
        return {"ok": True, "synced": stats}
    except Exception as e:
        raise _sanitize_500(e, "sync_orm_mirror")


# Spiegel-Tabellenname → ORM-Modell, in sync_all-Reihenfolge (der 19-Tabellen-Lesespiegel).
_MIRROR_TABLES = {
    "employees": "Employee",
    "groups": "Group",
    "group_assignments": "GroupAssignment",
    "shifts": "Shift",
    "leave_types": "LeaveType",
    "workplaces": "Workplace",
    "shift_assignments": "ShiftAssignment",
    "special_shifts": "SpecialShift",
    "absences": "Absence",
    "holidays": "Holiday",
    "periods": "Period",
    "bookings": "AccountBooking",
    "overtime": "OvertimeEntry",
    "leave_entitlements": "LeaveEntitlement",
    "shift_demand": "ShiftDemand",
    "special_demand": "SpecialDemand",
    "cycles": "Cycle",
    "cycle_assignments": "CycleAssignment",
    "restrictions": "Restriction",
}


@router.get("/status")
def orm_mirror_status(user: dict = Depends(require_admin)):
    """Meldet den aktuellen ORM-Spiegel-Zustand ohne Neu-Sync.

    Liefert, ob die Spiegel-DB materialisiert ist, plus die Zeilenzahlen je
    Tabelle (alle 19), damit Admins die Frische des Spiegels billig einschätzen
    können, bevor sie POST /sync auslösen.
    """
    import sp5lib.orm as orm
    from sqlalchemy import func, select

    orm_db = state_path("sp5_orm.db")
    mirror_exists = os.path.exists(orm_db)

    session = _get_orm_session()
    try:
        counts = {
            name: session.scalar(select(func.count()).select_from(getattr(orm, model)))
            for name, model in _MIRROR_TABLES.items()
        }
        return {
            "mirror_db_exists": mirror_exists,
            "table_count": len(counts),
            "total_rows": sum(counts.values()),
            "counts": counts,
        }
    except Exception as e:
        raise _sanitize_500(e, "orm_mirror_status")
    finally:
        session.close()


@router.get("/shifts")
def list_orm_shifts(include_hidden: bool = False, user: dict = Depends(require_admin)):
    """Listet Schichtdefinitionen aus dem ORM-Spiegel (DBF-förmige dicts)."""
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
    """Listet Abwesenheitsarten aus dem ORM-Spiegel (DBF-förmige dicts)."""
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
    """Listet Arbeitsplatzdefinitionen aus dem ORM-Spiegel (DBF-förmige dicts)."""
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
    """Listet reguläre Planeinträge (5MASHI) aus dem ORM-Spiegel, filterbar
    nach Zeitraum und/oder Mitarbeiter."""
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
    """Listet Sonderdienste (5SPSHI) aus dem ORM-Spiegel, filterbar nach
    Zeitraum und/oder Mitarbeiter."""
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
    """Listet Abwesenheiten (5ABSEN) aus dem ORM-Spiegel, filterbar nach
    Zeitraum und/oder Mitarbeiter."""
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


# ── Calendar data (lib 1.4.0) — holidays & periods ───────────────


@router.get("/holidays")
def list_orm_holidays(
    year: int | None = Query(
        None, description="Restrict to this calendar year (plus recurring holidays)"
    ),
    user: dict = Depends(require_admin),
):
    """Listet Feiertage (5HOLID) aus dem ORM-Spiegel (DBF-förmige dicts).

    Mit ``year`` kommen die Feiertage des Jahres plus alle wiederkehrenden."""
    from sp5lib.orm.repository import HolidayRepository

    session = _get_orm_session()
    try:
        return [h.to_dict() for h in HolidayRepository(session).list(year=year)]
    except Exception as e:
        raise _sanitize_500(e, "list_orm_holidays")
    finally:
        session.close()


@router.get("/periods")
def list_orm_periods(user: dict = Depends(require_admin)):
    """Listet Abrechnungs-/Planungszeiträume (5PERIO) aus dem ORM-Spiegel."""
    from sp5lib.orm.repository import PeriodRepository

    session = _get_orm_session()
    try:
        return [p.to_dict() for p in PeriodRepository(session).list()]
    except Exception as e:
        raise _sanitize_500(e, "list_orm_periods")
    finally:
        session.close()


# ── Time accounting (lib 1.5.0) — bookings / overtime / entitlements ──


@router.get("/bookings")
def list_orm_bookings(
    date_from: str | None = Query(None, description="ISO date (inclusive lower bound)"),
    date_to: str | None = Query(None, description="ISO date (inclusive upper bound)"),
    employee_id: int | None = Query(None, description="Filter by employee ID"),
    user: dict = Depends(require_admin),
):
    """List manual account / time bookings (5BOOK), filterable by date range
    and/or employee."""
    from sp5lib.orm.repository import AccountBookingRepository

    session = _get_orm_session()
    try:
        rows = AccountBookingRepository(session).list(
            date_from=date_from, date_to=date_to, employee_id=employee_id
        )
        return [r.to_dict() for r in rows]
    except Exception as e:
        raise _sanitize_500(e, "list_orm_bookings")
    finally:
        session.close()


@router.get("/overtime")
def list_orm_overtime(
    date_from: str | None = Query(None, description="ISO date (inclusive lower bound)"),
    date_to: str | None = Query(None, description="ISO date (inclusive upper bound)"),
    employee_id: int | None = Query(None, description="Filter by employee ID"),
    user: dict = Depends(require_admin),
):
    """List manual overtime adjustments (5OVER), filterable by date range
    and/or employee."""
    from sp5lib.orm.repository import OvertimeEntryRepository

    session = _get_orm_session()
    try:
        rows = OvertimeEntryRepository(session).list(
            date_from=date_from, date_to=date_to, employee_id=employee_id
        )
        return [r.to_dict() for r in rows]
    except Exception as e:
        raise _sanitize_500(e, "list_orm_overtime")
    finally:
        session.close()


@router.get("/leave-entitlements")
def list_orm_leave_entitlements(
    year: int | None = Query(None, description="Filter by entitlement year"),
    employee_id: int | None = Query(None, description="Filter by employee ID"),
    user: dict = Depends(require_admin),
):
    """Listet Jahres-Urlaubsansprüche (5LEAEN), filterbar nach Jahr und/oder
    Mitarbeiter."""
    from sp5lib.orm.repository import LeaveEntitlementRepository

    session = _get_orm_session()
    try:
        rows = LeaveEntitlementRepository(session).list(year=year, employee_id=employee_id)
        return [r.to_dict() for r in rows]
    except Exception as e:
        raise _sanitize_500(e, "list_orm_leave_entitlements")
    finally:
        session.close()


# ── Planning data (lib 1.6.0) — demand / cycles / restrictions ───────


@router.get("/shift-demands")
def list_orm_shift_demands(
    shift_id: int | None = Query(None, description="Filter by shift ID"),
    weekday: int | None = Query(None, description="Filter by weekday (0=Mon … 6=Sun)"),
    group_id: int | None = Query(None, description="Filter by group ID"),
    user: dict = Depends(require_admin),
):
    """List per-weekday staffing demand (5SHDEM), filterable by shift, weekday
    and/or group."""
    from sp5lib.orm.repository import ShiftDemandRepository

    session = _get_orm_session()
    try:
        rows = ShiftDemandRepository(session).list(
            shift_id=shift_id, weekday=weekday, group_id=group_id
        )
        return [r.to_dict() for r in rows]
    except Exception as e:
        raise _sanitize_500(e, "list_orm_shift_demands")
    finally:
        session.close()


@router.get("/special-demands")
def list_orm_special_demands(
    date_from: str | None = Query(None, description="ISO date (inclusive lower bound)"),
    date_to: str | None = Query(None, description="ISO date (inclusive upper bound)"),
    shift_id: int | None = Query(None, description="Filter by shift ID"),
    user: dict = Depends(require_admin),
):
    """List date-specific staffing demand (5SPDEM), filterable by date range
    and/or shift."""
    from sp5lib.orm.repository import SpecialDemandRepository

    session = _get_orm_session()
    try:
        rows = SpecialDemandRepository(session).list(
            date_from=date_from, date_to=date_to, shift_id=shift_id
        )
        return [r.to_dict() for r in rows]
    except Exception as e:
        raise _sanitize_500(e, "list_orm_special_demands")
    finally:
        session.close()


@router.get("/cycles")
def list_orm_cycles(include_hidden: bool = False, user: dict = Depends(require_admin)):
    """Listet Zyklus-Definitionen (5CYCLE) aus dem ORM-Spiegel."""
    from sp5lib.orm.repository import CycleRepository

    session = _get_orm_session()
    try:
        return [c.to_dict() for c in CycleRepository(session).list(include_hidden=include_hidden)]
    except Exception as e:
        raise _sanitize_500(e, "list_orm_cycles")
    finally:
        session.close()


@router.get("/cycle-assignments")
def list_orm_cycle_assignments(
    employee_id: int | None = Query(None, description="Filter by employee ID"),
    cycle_id: int | None = Query(None, description="Filter by cycle ID"),
    user: dict = Depends(require_admin),
):
    """Listet MA↔Zyklus-Zuweisungen (5CYASS), filterbar nach Mitarbeiter
    und/oder Zyklus."""
    from sp5lib.orm.repository import CycleAssignmentRepository

    session = _get_orm_session()
    try:
        rows = CycleAssignmentRepository(session).list(employee_id=employee_id, cycle_id=cycle_id)
        return [r.to_dict() for r in rows]
    except Exception as e:
        raise _sanitize_500(e, "list_orm_cycle_assignments")
    finally:
        session.close()


@router.get("/restrictions")
def list_orm_restrictions(
    employee_id: int | None = Query(None, description="Filter by employee ID"),
    shift_id: int | None = Query(None, description="Filter by shift ID"),
    user: dict = Depends(require_admin),
):
    """Listet Einsatz-Einschränkungen (5RESTR), filterbar nach Mitarbeiter
    und/oder Schicht."""
    from sp5lib.orm.repository import RestrictionRepository

    session = _get_orm_session()
    try:
        rows = RestrictionRepository(session).list(employee_id=employee_id, shift_id=shift_id)
        return [r.to_dict() for r in rows]
    except Exception as e:
        raise _sanitize_500(e, "list_orm_restrictions")
    finally:
        session.close()
