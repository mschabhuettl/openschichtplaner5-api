"""Tests for the schedule PDF (print-view) endpoint.

GET /api/v1/schedule/pdf?year=YYYY&month=MM[&group_id=X]

Returns a print-optimized HTML page for use with browser Ctrl+P → PDF.
Requires Planer+ role.
"""

import secrets

# ── Helpers ─────────────────────────────────────────────────────


def _planer_token(sessions) -> str:
    tok = secrets.token_hex(20)
    sessions[tok] = {
        "ID": 801,
        "NAME": "test_planer_pdf",
        "role": "Planer",
        "ADMIN": False,
        "RIGHTS": 2,
    }
    return tok


def _leser_token(sessions) -> str:
    tok = secrets.token_hex(20)
    sessions[tok] = {
        "ID": 802,
        "NAME": "test_leser_pdf",
        "role": "Leser",
        "ADMIN": False,
        "RIGHTS": 1,
    }
    return tok


# ── Tests ────────────────────────────────────────────────────────


class TestSchedulePdfBasic:
    """Basic response / content-type checks."""

    def test_returns_200_with_planer(self, sync_client):
        """Planer role should get a 200 HTML response."""
        from api.main import _sessions
        tok = _planer_token(_sessions)
        resp = sync_client.get(
            "/api/v1/schedule/pdf?year=2024&month=1",
            headers={"X-Auth-Token": tok},
        )
        _sessions.pop(tok, None)
        assert resp.status_code == 200

    def test_content_type_is_html(self, sync_client):
        """Response must have text/html content-type."""
        from api.main import _sessions
        tok = _planer_token(_sessions)
        resp = sync_client.get(
            "/api/v1/schedule/pdf?year=2024&month=1",
            headers={"X-Auth-Token": tok},
        )
        _sessions.pop(tok, None)
        assert "text/html" in resp.headers.get("content-type", "")

    def test_html_contains_doctype(self, sync_client):
        """Response body must start with an HTML doctype."""
        from api.main import _sessions
        tok = _planer_token(_sessions)
        resp = sync_client.get(
            "/api/v1/schedule/pdf?year=2024&month=1",
            headers={"X-Auth-Token": tok},
        )
        _sessions.pop(tok, None)
        assert resp.status_code == 200
        assert resp.text.strip().lower().startswith("<!doctype html")

    def test_html_contains_month_year(self, sync_client):
        """HTML must mention the requested month and year."""
        from api.main import _sessions
        tok = _planer_token(_sessions)
        resp = sync_client.get(
            "/api/v1/schedule/pdf?year=2024&month=3",
            headers={"X-Auth-Token": tok},
        )
        _sessions.pop(tok, None)
        assert resp.status_code == 200
        assert "2024" in resp.text
        assert "März" in resp.text  # German month name

    def test_html_contains_table(self, sync_client):
        """HTML must contain a <table> element for the schedule grid."""
        from api.main import _sessions
        tok = _planer_token(_sessions)
        resp = sync_client.get(
            "/api/v1/schedule/pdf?year=2024&month=6",
            headers={"X-Auth-Token": tok},
        )
        _sessions.pop(tok, None)
        assert resp.status_code == 200
        assert "<table" in resp.text.lower()

    def test_html_contains_a4_landscape_css(self, sync_client):
        """HTML must declare A4 landscape page layout for printing."""
        from api.main import _sessions
        tok = _planer_token(_sessions)
        resp = sync_client.get(
            "/api/v1/schedule/pdf?year=2024&month=1",
            headers={"X-Auth-Token": tok},
        )
        _sessions.pop(tok, None)
        assert resp.status_code == 200
        body = resp.text
        assert "A4" in body
        assert "landscape" in body


class TestSchedulePdfAuth:
    """Authentication and authorization checks."""

    def test_requires_auth(self, app):
        """Unauthenticated request must return 401."""
        from starlette.testclient import TestClient
        with TestClient(app, raise_server_exceptions=True) as c:
            resp = c.get("/api/v1/schedule/pdf?year=2024&month=1")
        assert resp.status_code == 401

    def test_leser_role_forbidden(self, sync_client):
        """Leser (read-only) role must be rejected with 403."""
        from api.main import _sessions
        tok = _leser_token(_sessions)
        resp = sync_client.get(
            "/api/v1/schedule/pdf?year=2024&month=1",
            headers={"X-Auth-Token": tok},
        )
        _sessions.pop(tok, None)
        assert resp.status_code == 403

    def test_admin_role_allowed(self, sync_client):
        """Admin role must be allowed (Admin ≥ Planer)."""
        # sync_client already uses Admin token
        resp = sync_client.get("/api/v1/schedule/pdf?year=2024&month=1")
        assert resp.status_code == 200


class TestSchedulePdfValidation:
    """Query parameter validation checks."""

    def test_missing_year_returns_422(self, sync_client):
        """Missing year parameter must return 422."""
        resp = sync_client.get("/api/v1/schedule/pdf?month=1")
        assert resp.status_code == 422

    def test_missing_month_returns_422(self, sync_client):
        """Missing month parameter must return 422."""
        resp = sync_client.get("/api/v1/schedule/pdf?year=2024")
        assert resp.status_code == 422

    def test_invalid_month_zero(self, sync_client):
        """Month=0 must be rejected (ge=1 constraint)."""
        resp = sync_client.get("/api/v1/schedule/pdf?year=2024&month=0")
        assert resp.status_code == 422

    def test_invalid_month_thirteen(self, sync_client):
        """Month=13 must be rejected (le=12 constraint)."""
        resp = sync_client.get("/api/v1/schedule/pdf?year=2024&month=13")
        assert resp.status_code == 422

    def test_invalid_year_too_old(self, sync_client):
        """Year < 2000 must be rejected."""
        resp = sync_client.get("/api/v1/schedule/pdf?year=1999&month=1")
        assert resp.status_code == 422

    def test_invalid_group_id_404(self, sync_client):
        """Non-existent group_id must return 404."""
        resp = sync_client.get(
            "/api/v1/schedule/pdf?year=2024&month=1&group_id=999999"
        )
        assert resp.status_code == 404

    def test_all_months_accessible(self, sync_client):
        """All 12 months should be accessible without error."""
        for month in range(1, 13):
            resp = sync_client.get(f"/api/v1/schedule/pdf?year=2024&month={month}")
            assert resp.status_code == 200, f"Month {month} failed: {resp.status_code}"


class TestSchedulePdfContent:
    """Content correctness checks."""

    def test_empty_schedule_no_crash(self, sync_client):
        """Future month with no data must return valid HTML, not crash."""
        resp = sync_client.get("/api/v1/schedule/pdf?year=2099&month=12")
        assert resp.status_code == 200
        assert "<html" in resp.text.lower()

    def test_html_contains_day_numbers(self, sync_client):
        """The schedule table header must include day numbers 1 through 28+."""
        from api.main import _sessions
        tok = _planer_token(_sessions)
        resp = sync_client.get(
            "/api/v1/schedule/pdf?year=2024&month=2",  # Feb 2024 = 29 days
            headers={"X-Auth-Token": tok},
        )
        _sessions.pop(tok, None)
        assert resp.status_code == 200
        body = resp.text
        # All days 1–29 should appear in the table headers
        for d in range(1, 30):
            assert str(d) in body

    def test_html_contains_print_css(self, sync_client):
        """HTML must contain @media print CSS block."""
        from api.main import _sessions
        tok = _planer_token(_sessions)
        resp = sync_client.get(
            "/api/v1/schedule/pdf?year=2024&month=1",
            headers={"X-Auth-Token": tok},
        )
        _sessions.pop(tok, None)
        assert resp.status_code == 200
        assert "@media print" in resp.text

    def test_versioned_and_unversioned_both_work(self, sync_client):
        """Both /api/schedule/pdf and /api/v1/schedule/pdf should return 200."""
        r1 = sync_client.get("/api/schedule/pdf?year=2024&month=1")
        r2 = sync_client.get("/api/v1/schedule/pdf?year=2024&month=1")
        assert r1.status_code == 200
        assert r2.status_code == 200


# ── _build_schedule_html unit tests (entries-present + group paths) ──────────


class _StubDB:
    """Minimal db stub for exercising _build_schedule_html directly."""

    def __init__(self, entries, groups=None):
        self._entries = entries
        self._groups = groups or []

    def get_schedule(self, year, month, group_id=None):
        return self._entries

    def get_groups(self):
        return self._groups

    def get_employees(self, include_hidden=False):
        return []

    def get_group_members(self, gid):
        return []


class TestBuildScheduleHtml:
    """Directly exercise the HTML builder for the with-entries / group branches."""

    def _entries(self):
        return [
            {  # a shift entry → shift_short label
                "employee_id": 1,
                "employee_name": "Müller, Anna",
                "employee_short": "MA",
                "date": "2024-07-15",
                "kind": "shift",
                "shift_short": "F",
                "shift_name": "Frühschicht",
            },
            {  # an absence entry → leave_short label
                "employee_id": 2,
                "employee_name": "Bauer, Tom",
                "employee_short": "BT",
                "date": "2024-07-16",
                "kind": "absence",
                "leave_short": "U",
                "leave_name": "Urlaub",
            },
            {  # display_name branch + out-of-range/short date guards
                "employee_id": 1,
                "employee_name": "Müller, Anna",
                "date": "2024-07-31",
                "kind": "shift",
                "display_name": "X",
            },
        ]

    def test_renders_grid_with_entries(self):
        from api.routers.schedule_pdf import _build_schedule_html

        html = _build_schedule_html(2024, 7, None, _StubDB(self._entries()))
        assert html.strip().lower().startswith("<!doctype html")
        assert "<table" in html
        assert "Müller" in html and "Bauer" in html
        assert "F" in html and "U" in html  # shift + absence labels
        assert "Juli" in html and "2024" in html

    def test_group_name_shown_when_group_given(self):
        from api.routers.schedule_pdf import _build_schedule_html

        groups = [{"ID": 5, "NAME": "Team Nord"}]
        html = _build_schedule_html(2024, 7, 5, _StubDB(self._entries(), groups))
        assert "Team Nord" in html

    def test_ignores_bad_dates(self):
        from api.routers.schedule_pdf import _build_schedule_html

        bad = [
            {"employee_id": 1, "employee_name": "A", "date": "bad", "kind": "shift", "shift_short": "F"},
            {"employee_id": 1, "employee_name": "A", "date": "2024-07-99", "kind": "shift", "shift_short": "F"},
        ]
        html = _build_schedule_html(2024, 7, None, _StubDB(bad))
        assert "<table" in html  # builds without raising


class TestSchedulePdfGroupValidation:
    def test_invalid_group_returns_404(self, sync_client):
        from api.main import _sessions

        tok = _planer_token(_sessions)
        resp = sync_client.get(
            "/api/v1/schedule/pdf?year=2024&month=7&group_id=999999",
            headers={"X-Auth-Token": tok},
        )
        _sessions.pop(tok, None)
        assert resp.status_code == 404
