"""Tests for backend pagination on list endpoints."""

from api.schemas import paginate
from starlette.testclient import TestClient


class TestPaginateHelper:
    """Unit tests for the paginate() utility."""

    def test_no_page_returns_list(self):
        data = [1, 2, 3]
        result = paginate(data, page=None)
        assert isinstance(result, list)
        assert result == [1, 2, 3]

    def test_page_returns_dict(self):
        data = list(range(1, 11))
        result = paginate(data, page=1, page_size=3)
        assert isinstance(result, dict)
        assert result["items"] == [1, 2, 3]
        assert result["total"] == 10
        assert result["page"] == 1
        assert result["page_size"] == 3
        assert result["pages"] == 4  # ceil(10/3)

    def test_last_page(self):
        data = list(range(1, 11))
        result = paginate(data, page=4, page_size=3)
        assert result["items"] == [10]
        assert result["page"] == 4

    def test_beyond_last_page(self):
        data = list(range(1, 6))
        result = paginate(data, page=99, page_size=5)
        assert result["items"] == []
        assert result["total"] == 5

    def test_empty_data(self):
        result = paginate([], page=1, page_size=10)
        assert result["items"] == []
        assert result["total"] == 0
        assert result["pages"] == 1

    def test_page_size_clamped(self):
        data = list(range(100))
        result = paginate(data, page=1, page_size=9999)
        assert result["page_size"] == 500  # max clamp


class TestEmployeesPagination:
    def test_unpaginated_returns_list(self, sync_client: TestClient):
        res = sync_client.get("/api/employees")
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_paginated_returns_envelope(self, sync_client: TestClient):
        res = sync_client.get("/api/employees?page=1&page_size=2")
        assert res.status_code == 200
        data = res.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data
        assert "pages" in data
        assert data["page"] == 1
        assert data["page_size"] == 2

    def test_page_size_limits_results(self, sync_client: TestClient):
        res = sync_client.get("/api/employees?page=1&page_size=1")
        data = res.json()
        assert len(data["items"]) <= 1


class TestAbsencesPagination:
    def test_unpaginated_returns_list(self, sync_client: TestClient):
        res = sync_client.get("/api/absences")
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_paginated_returns_envelope(self, sync_client: TestClient):
        res = sync_client.get("/api/absences?page=1&page_size=5")
        assert res.status_code == 200
        data = res.json()
        assert "items" in data
        assert data["page"] == 1


class TestChangelogPagination:
    def test_unpaginated_returns_list(self, sync_client: TestClient):
        res = sync_client.get("/api/changelog")
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_paginated_returns_envelope(self, sync_client: TestClient):
        res = sync_client.get("/api/changelog?page=1&page_size=10")
        assert res.status_code == 200
        data = res.json()
        assert "items" in data
        assert data["page"] == 1


class TestListLimitBounds:
    """Guard the ge/le bounds on list-endpoint `limit` params.

    The underlying ``get_changelog`` issues a raw ``stmt.limit(limit)`` with no
    clamping, so an unbounded ``limit`` would materialise the whole audit log
    (a DoS vector) and — on SQLite — a negative ``limit`` means *unlimited*.
    These bounds reject both at the edge with a clean 422 while leaving valid
    requests untouched.
    """

    def test_changelog_limit_zero_rejected(self, sync_client: TestClient):
        assert sync_client.get("/api/changelog?limit=0").status_code == 422

    def test_changelog_limit_negative_rejected(self, sync_client: TestClient):
        # SQLite treats LIMIT -1 as "no limit" — ge=1 must reject it.
        assert sync_client.get("/api/changelog?limit=-1").status_code == 422

    def test_changelog_limit_too_high_rejected(self, sync_client: TestClient):
        assert sync_client.get("/api/changelog?limit=10000").status_code == 422

    def test_changelog_limit_at_max_accepted(self, sync_client: TestClient):
        assert sync_client.get("/api/changelog?limit=5000").status_code == 200

    def test_changelog_default_limit_accepted(self, sync_client: TestClient):
        assert sync_client.get("/api/changelog?limit=100").status_code == 200

    def test_notifications_limit_zero_rejected(self, sync_client: TestClient):
        assert sync_client.get("/api/notifications?limit=0").status_code == 422

    def test_notifications_limit_valid_accepted(self, sync_client: TestClient):
        assert sync_client.get("/api/notifications?limit=50").status_code == 200

    def test_notifications_all_limit_zero_rejected(self, sync_client: TestClient):
        assert sync_client.get("/api/notifications/all?limit=0").status_code == 422

    def test_notifications_all_limit_valid_accepted(self, sync_client: TestClient):
        assert sync_client.get("/api/notifications/all?limit=100").status_code == 200
