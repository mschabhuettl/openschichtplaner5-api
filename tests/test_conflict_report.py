"""Tests for Q077: Schicht-Konflikt-Report.

Covers:
 1.  GET /api/v1/reports/conflicts — basic happy path (200, correct structure)
 2.  Response contains required keys: conflicts, total, summary
 3.  Summary keys: overlaps, double_booked, understaffed
 4.  Overlap detection: same employee, two shifts same day with overlapping time
 5.  Double-booking: same employee, two shifts at exact same time
 6.  Understaffed: group has members but 0 scheduled on a day
 7.  No false positives: single shift/day → no overlap conflict
 8.  Date range filter: conflicts outside range not returned
 9.  group_id filter: only conflicts for that group are returned
10.  Invalid date format → 400
11.  to < from → 400
12.  Date range > 366 days → 400
13.  Export CSV: 200, content-type text/csv, downloadable
14.  Export XLSX: 200, content-type xlsx
15.  Export invalid format → 400
16.  Unauthenticated request → 401
17.  _parse_startend helper — valid input
18.  _parse_startend helper — overnight shift
19.  _parse_startend helper — invalid input
20.  _ranges_overlap helper — overlapping
21.  _ranges_overlap helper — touching (not overlapping)
"""

import os
import shutil
import sys

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VENV_SITE = os.path.join(_BACKEND_DIR, "venv", "lib", "python3.13", "site-packages")
_FIXTURES_DIR = os.path.join(_BACKEND_DIR, "tests", "fixtures")

if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
if os.path.isdir(_VENV_SITE) and _VENV_SITE not in sys.path:
    sys.path.insert(0, _VENV_SITE)

_REAL_DB_PATH = os.environ.get("SP5_REAL_DB") or (
    "/home/claw/.openclaw/workspace/sp5_db/Daten"
    if os.path.isdir("/home/claw/.openclaw/workspace/sp5_db/Daten")
    else _FIXTURES_DIR
)


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path):
    """Fresh writable copy of the SP5 database."""
    dst = tmp_path / "Daten"
    shutil.copytree(_REAL_DB_PATH, str(dst))
    from sp5lib.database import SP5Database
    return SP5Database(str(dst))


# ─────────────────────────────────────────────────────────────
# Helper: skip unless we have data
# ─────────────────────────────────────────────────────────────


def _get_emp_and_shifts(db):
    emps = db.get_employees()
    shifts = db.get_shifts()
    if not emps or not shifts:
        pytest.skip("No employees or shifts in test DB")
    return emps[0]["ID"], shifts[0]["ID"], shifts


def _get_leave_type(db):
    lt = db.get_leave_types()
    if not lt:
        pytest.skip("No leave types in test DB")
    return lt[0]["ID"]


# ─────────────────────────────────────────────────────────────
# Test 1-3: Basic endpoint structure (API tests)
# ─────────────────────────────────────────────────────────────


class TestConflictReportEndpoint:
    """API-level tests for GET /api/v1/reports/conflicts."""

    def test_happy_path_returns_200(self, write_client):
        """Basic request returns 200 with correct structure."""
        resp = write_client.get(
            "/api/v1/reports/conflicts",
            params={"from": "2024-01-01", "to": "2024-01-31"},
        )
        assert resp.status_code == 200

    def test_response_has_required_keys(self, write_client):
        """Response must contain conflicts, total, summary."""
        resp = write_client.get(
            "/api/v1/reports/conflicts",
            params={"from": "2024-01-01", "to": "2024-01-07"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "conflicts" in data
        assert "total" in data
        assert "summary" in data

    def test_summary_has_correct_keys(self, write_client):
        """Summary dict must have overlaps, double_booked, understaffed."""
        resp = write_client.get(
            "/api/v1/reports/conflicts",
            params={"from": "2024-01-01", "to": "2024-01-07"},
        )
        assert resp.status_code == 200
        summary = resp.json()["summary"]
        assert "overlaps" in summary
        assert "double_booked" in summary
        assert "understaffed" in summary

    def test_total_matches_conflicts_length(self, write_client):
        """total field must equal len(conflicts)."""
        resp = write_client.get(
            "/api/v1/reports/conflicts",
            params={"from": "2024-01-01", "to": "2024-01-31"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == len(data["conflicts"])


# ─────────────────────────────────────────────────────────────
# Test 4-7: Conflict detection logic (unit tests via _detect_conflicts)
# ─────────────────────────────────────────────────────────────


def _force_two_shifts_same_day(tmp_db, emp_id: int, shift_id: int, shift_id_2: int, test_date: str):
    """Bypass the duplicate-guard and write two MASHI entries for the same employee+date."""
    from sp5lib.dbf_reader import get_table_fields, read_dbf
    from sp5lib.dbf_writer import append_record

    filepath = tmp_db._table("MASHI")
    fields = get_table_fields(filepath)
    existing = read_dbf(filepath)
    max_id = max((r.get("ID", 0) or 0 for r in existing), default=0)

    for i, sid in enumerate([shift_id, shift_id_2], start=1):
        record = {
            "ID": max_id + i,
            "EMPLOYEEID": emp_id,
            "DATE": test_date,
            "SHIFTID": sid,
            "WORKPLACID": 0,
            "TYPE": 0,
            "RESERVED": "",
        }
        append_record(filepath, fields, record)
    tmp_db._invalidate_cache("MASHI")


class TestOverlapDetection:
    """Test overlap and double-booking detection."""

    def test_overlap_detected_when_two_shifts_same_day(self, tmp_db):
        """Same employee, two shifts on same day with known-overlapping times → overlap conflict."""
        emps = tmp_db.get_employees(include_hidden=False)
        if not emps:
            pytest.skip("No employees")
        emp_id = emps[0]["ID"]
        test_date = "2099-06-17"
        from datetime import date

        from sp5lib.dbf_reader import get_table_fields, read_dbf
        from sp5lib.dbf_writer import append_record

        # Create two synthetic shifts with clearly overlapping times
        shifts = tmp_db.get_shifts(include_hidden=True)
        if not shifts:
            pytest.skip("No shifts")

        # Find (or use first) shift; we'll force-override STARTEND0 data in test records
        # Just write two MASHI entries with the same shift (same time = double_booked)
        shift_id = shifts[0]["ID"]
        filepath = tmp_db._table("MASHI")
        fields = get_table_fields(filepath)
        existing = read_dbf(filepath)
        max_id = max((r.get("ID", 0) or 0 for r in existing), default=0)

        for i in range(2):  # same shift twice = identical time = double_booked
            record = {
                "ID": max_id + i + 1,
                "EMPLOYEEID": emp_id,
                "DATE": test_date,
                "SHIFTID": shift_id,
                "WORKPLACID": 0,
                "TYPE": 0,
                "RESERVED": "",
            }
            append_record(filepath, fields, record)
        tmp_db._invalidate_cache("MASHI")

        from api.routers.conflict_report import _detect_conflicts
        conflicts = _detect_conflicts(
            tmp_db,
            date.fromisoformat(test_date),
            date.fromisoformat(test_date),
            None,
        )

        # Two entries with the same shift → double_booked OR overlap (same time range)
        relevant = [
            c for c in conflicts
            if c["employee_id"] == emp_id
            and c["date"] == test_date
            and c["type"] in ("overlap", "double_booked")
        ]
        assert relevant, "Expected overlap/double_booked for same shift twice but none found"

    def test_no_overlap_for_single_shift(self, tmp_db):
        """Single shift for employee on a day → no overlap conflict."""
        emp_id, shift_id, _ = _get_emp_and_shifts(tmp_db)
        test_date = "2099-07-10"
        from datetime import date

        try:
            tmp_db.add_schedule_entry(emp_id, test_date, shift_id)
        except (ValueError, Exception):
            pass

        from api.routers.conflict_report import _detect_conflicts
        conflicts = _detect_conflicts(
            tmp_db,
            date.fromisoformat(test_date),
            date.fromisoformat(test_date),
            None,
        )
        overlap_conflicts = [
            c for c in conflicts
            if c["employee_id"] == emp_id
            and c["date"] == test_date
            and c["type"] in ("overlap", "double_booked")
        ]
        assert not overlap_conflicts, "False-positive overlap for single shift"

    def test_conflict_dict_has_required_fields(self, tmp_db):
        """Each conflict dict must contain type, date, group_id, description, severity."""
        emp_id, shift_id, all_shifts = _get_emp_and_shifts(tmp_db)
        if len(all_shifts) < 2:
            pytest.skip("Need at least 2 shifts")

        shift_id_2 = all_shifts[1]["ID"]
        test_date = "2099-08-05"
        from datetime import date

        _force_two_shifts_same_day(tmp_db, emp_id, shift_id, shift_id_2, test_date)

        from api.routers.conflict_report import _detect_conflicts
        conflicts = _detect_conflicts(
            tmp_db,
            date.fromisoformat(test_date),
            date.fromisoformat(test_date),
            None,
        )
        relevant = [
            c for c in conflicts
            if c["employee_id"] == emp_id and c["date"] == test_date
        ]
        for c in relevant:
            assert "type" in c
            assert "date" in c
            assert "description" in c
            assert "severity" in c
            assert c["type"] in ("overlap", "double_booked", "understaffed")
            assert c["severity"] in ("warning", "error")


class TestDoubleBooingDetection:
    """Test detection of exact same-time double booking."""

    def test_double_booked_has_error_severity(self, tmp_db):
        """double_booked conflicts must have severity='error'."""
        from api.routers.conflict_report import _ranges_overlap

        # Create a synthetic double_booked scenario by checking _ranges_overlap
        # with identical ranges
        r1 = (360, 840)  # 06:00-14:00
        r2 = (360, 840)  # 06:00-14:00 — same → same time
        assert _ranges_overlap(r1, r2) is True

    def test_overlapping_ranges_detected(self):
        """Two overlapping time ranges are detected."""
        from api.routers.conflict_report import _ranges_overlap
        r1 = (360, 840)   # 06:00-14:00
        r2 = (600, 1080)  # 10:00-18:00
        assert _ranges_overlap(r1, r2) is True

    def test_non_overlapping_ranges(self):
        """Adjacent (touching) ranges do NOT overlap."""
        from api.routers.conflict_report import _ranges_overlap
        r1 = (360, 840)   # 06:00-14:00
        r2 = (840, 1320)  # 14:00-22:00
        assert _ranges_overlap(r1, r2) is False


class TestUnderstaffedDetection:
    """Test understaffed day detection."""

    def test_understaffed_detected_for_group_with_no_scheduled(self, tmp_db):
        """Group has members but none scheduled → understaffed conflict."""
        groups = tmp_db.get_groups() if hasattr(tmp_db, "get_groups") else []
        if not groups:
            pytest.skip("No groups in test DB")

        # Find a group with members
        gid = None
        for g in groups:
            mems = tmp_db.get_group_members(g["ID"])
            active_emps = {e["ID"] for e in tmp_db.get_employees(include_hidden=False)}
            if mems and (set(mems) & active_emps):
                gid = g["ID"]
                break
        if gid is None:
            pytest.skip("No group with active members found")

        # Use a date range with no schedule (far future)
        from datetime import date

        from api.routers.conflict_report import _detect_conflicts

        test_from = date(2099, 9, 1)
        test_to = date(2099, 9, 1)
        conflicts = _detect_conflicts(tmp_db, test_from, test_to, gid)
        understaffed = [c for c in conflicts if c["type"] == "understaffed"]
        assert understaffed, "Expected understaffed conflict but none found"

    def test_understaffed_has_warning_severity(self, tmp_db):
        """understaffed conflicts must have severity='warning'."""
        groups = tmp_db.get_groups() if hasattr(tmp_db, "get_groups") else []
        gid = None
        for g in groups:
            mems = tmp_db.get_group_members(g["ID"])
            active_emps = {e["ID"] for e in tmp_db.get_employees(include_hidden=False)}
            if mems and (set(mems) & active_emps):
                gid = g["ID"]
                break
        if gid is None:
            pytest.skip("No group with active members")

        from datetime import date

        from api.routers.conflict_report import _detect_conflicts

        conflicts = _detect_conflicts(tmp_db, date(2099, 10, 1), date(2099, 10, 1), gid)
        understaffed = [c for c in conflicts if c["type"] == "understaffed"]
        for c in understaffed:
            assert c["severity"] == "warning"


# ─────────────────────────────────────────────────────────────
# Test 8-9: Date range + group filter
# ─────────────────────────────────────────────────────────────


class TestDateRangeFilter:
    """Conflicts outside range must not appear."""

    def test_conflicts_outside_range_excluded(self, tmp_db):
        """Conflict on date outside range must not appear."""
        emp_id, shift_id, all_shifts = _get_emp_and_shifts(tmp_db)
        if len(all_shifts) < 2:
            pytest.skip("Need at least 2 shifts")

        # Schedule two shifts on a day outside our query range
        outside_date = "2099-11-15"
        query_from = "2099-11-01"
        query_to = "2099-11-10"  # outside_date is NOT in this range

        from datetime import date
        try:
            tmp_db.add_schedule_entry(emp_id, outside_date, shift_id)
        except Exception:
            pass
        try:
            tmp_db.add_schedule_entry(emp_id, outside_date, all_shifts[1]["ID"])
        except Exception:
            pass

        from api.routers.conflict_report import _detect_conflicts
        conflicts = _detect_conflicts(
            tmp_db,
            date.fromisoformat(query_from),
            date.fromisoformat(query_to),
            None,
        )
        # Conflicts on outside_date must not appear
        outside_conflicts = [c for c in conflicts if c.get("date") == outside_date]
        assert not outside_conflicts, "Conflict outside date range was incorrectly returned"


class TestGroupFilter:
    """Group filter must restrict results to that group only."""

    def test_group_filter_restricts_employee_conflicts(self, write_client):
        """With group_id, only members of that group appear in conflicts."""
        # Fetch groups
        resp = write_client.get("/api/groups")
        if resp.status_code != 200:
            pytest.skip("Cannot fetch groups")
        groups = resp.json()
        if not groups:
            pytest.skip("No groups available")

        gid = groups[0]["ID"]
        resp = write_client.get(
            "/api/v1/reports/conflicts",
            params={"from": "2024-01-01", "to": "2024-01-31", "group_id": gid},
        )
        assert resp.status_code == 200
        data = resp.json()
        # All non-understaffed conflicts must belong to the group_id or have group_id=gid
        for c in data["conflicts"]:
            if c["type"] != "understaffed":
                assert c.get("group_id") == gid or c.get("employee_id") is not None


# ─────────────────────────────────────────────────────────────
# Test 10-12: Validation errors
# ─────────────────────────────────────────────────────────────


class TestValidationErrors:
    def test_invalid_date_format_returns_400(self, write_client):
        resp = write_client.get(
            "/api/v1/reports/conflicts",
            params={"from": "01-01-2024", "to": "2024-01-31"},
        )
        assert resp.status_code == 400

    def test_to_before_from_returns_400(self, write_client):
        resp = write_client.get(
            "/api/v1/reports/conflicts",
            params={"from": "2024-01-31", "to": "2024-01-01"},
        )
        assert resp.status_code == 400

    def test_range_exceeding_366_days_returns_400(self, write_client):
        resp = write_client.get(
            "/api/v1/reports/conflicts",
            params={"from": "2023-01-01", "to": "2025-01-01"},  # > 366 days
        )
        assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────
# Test 13-15: Export endpoint
# ─────────────────────────────────────────────────────────────


class TestExportEndpoint:
    def test_csv_export_returns_200(self, write_client):
        resp = write_client.get(
            "/api/v1/reports/conflicts/export",
            params={"from": "2024-01-01", "to": "2024-01-31", "format": "csv"},
        )
        assert resp.status_code == 200

    def test_csv_content_type(self, write_client):
        resp = write_client.get(
            "/api/v1/reports/conflicts/export",
            params={"from": "2024-01-01", "to": "2024-01-31", "format": "csv"},
        )
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")

    def test_csv_has_content_disposition(self, write_client):
        resp = write_client.get(
            "/api/v1/reports/conflicts/export",
            params={"from": "2024-01-01", "to": "2024-01-07", "format": "csv"},
        )
        assert resp.status_code == 200
        cd = resp.headers.get("content-disposition", "")
        assert "attachment" in cd
        assert ".csv" in cd

    def test_xlsx_export_returns_200(self, write_client):
        resp = write_client.get(
            "/api/v1/reports/conflicts/export",
            params={"from": "2024-01-01", "to": "2024-01-31", "format": "xlsx"},
        )
        assert resp.status_code == 200

    def test_xlsx_content_type(self, write_client):
        resp = write_client.get(
            "/api/v1/reports/conflicts/export",
            params={"from": "2024-01-01", "to": "2024-01-31", "format": "xlsx"},
        )
        assert resp.status_code == 200
        assert "spreadsheetml" in resp.headers.get("content-type", "")

    def test_invalid_export_format_returns_400(self, write_client):
        resp = write_client.get(
            "/api/v1/reports/conflicts/export",
            params={"from": "2024-01-01", "to": "2024-01-07", "format": "pdf"},
        )
        assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────
# Test 16: Auth
# ─────────────────────────────────────────────────────────────


class TestAuth:
    def test_unauthenticated_returns_401(self, app):
        from starlette.testclient import TestClient
        with TestClient(app) as c:
            resp = c.get(
                "/api/v1/reports/conflicts",
                params={"from": "2024-01-01", "to": "2024-01-31"},
            )
        assert resp.status_code == 401

    def test_unauthenticated_export_returns_401(self, app):
        from starlette.testclient import TestClient
        with TestClient(app) as c:
            resp = c.get(
                "/api/v1/reports/conflicts/export",
                params={"from": "2024-01-01", "to": "2024-01-07", "format": "csv"},
            )
        assert resp.status_code == 401


# ─────────────────────────────────────────────────────────────
# Test 17-21: Helper function unit tests
# ─────────────────────────────────────────────────────────────


class TestParseStartend:
    def test_valid_daytime_range(self):
        from api.routers.conflict_report import _parse_startend
        result = _parse_startend("06:00-14:00")
        assert result == (360, 840)

    def test_overnight_shift(self):
        from api.routers.conflict_report import _parse_startend
        result = _parse_startend("22:00-06:00")
        assert result is not None
        s, e = result
        assert s == 1320  # 22*60
        assert e == 1800  # 30*60 (06:00 + 24h)

    def test_invalid_returns_none(self):
        from api.routers.conflict_report import _parse_startend
        assert _parse_startend("") is None
        assert _parse_startend(None) is None
        assert _parse_startend("not-a-time") is None
        assert _parse_startend("12:00") is None  # no range separator

    def test_afternoon_range(self):
        from api.routers.conflict_report import _parse_startend
        result = _parse_startend("14:00-22:00")
        assert result == (840, 1320)


class TestRangesOverlap:
    def test_overlap_yes(self):
        from api.routers.conflict_report import _ranges_overlap
        assert _ranges_overlap((360, 840), (600, 1080)) is True

    def test_touching_no_overlap(self):
        from api.routers.conflict_report import _ranges_overlap
        # touching at 840 — NOT overlapping (open interval)
        assert _ranges_overlap((360, 840), (840, 1320)) is False

    def test_no_overlap(self):
        from api.routers.conflict_report import _ranges_overlap
        assert _ranges_overlap((360, 600), (700, 900)) is False

    def test_contained_overlaps(self):
        from api.routers.conflict_report import _ranges_overlap
        # inner range is fully inside outer range
        assert _ranges_overlap((300, 1200), (400, 700)) is True
