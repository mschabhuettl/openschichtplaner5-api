"""Tests for the global search endpoint (/api/search) — Q061."""

from fastapi.testclient import TestClient


class TestGlobalSearch:
    """Tests for GET /api/search with limit and grouped parameters."""

    def test_search_empty_query(self, sync_client: TestClient):
        """Empty query returns empty results."""
        res = sync_client.get("/api/search?q=")
        assert res.status_code == 200
        data = res.json()
        assert data["results"] == []
        assert data["query"] == ""

    def test_search_empty_query_grouped(self, sync_client: TestClient):
        """Empty query with grouped=true returns empty grouped results."""
        res = sync_client.get("/api/search?q=&grouped=true")
        assert res.status_code == 200
        data = res.json()
        assert "employees" in data
        assert "groups" in data
        assert "shifts" in data
        assert "leave_types" in data
        assert data["employees"] == []

    def test_search_returns_results(self, sync_client: TestClient):
        """Non-empty query returns 200 with results array."""
        res = sync_client.get("/api/search?q=test")
        assert res.status_code == 200
        data = res.json()
        assert "results" in data
        assert isinstance(data["results"], list)
        assert "query" in data

    def test_search_limit_param(self, sync_client: TestClient):
        """limit parameter is accepted."""
        res = sync_client.get("/api/search?q=a&limit=2")
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data["results"], list)

    def test_search_limit_validation_too_low(self, sync_client: TestClient):
        """limit < 1 returns 422."""
        res = sync_client.get("/api/search?q=test&limit=0")
        assert res.status_code == 422

    def test_search_limit_validation_too_high(self, sync_client: TestClient):
        """limit > 50 returns 422."""
        res = sync_client.get("/api/search?q=test&limit=100")
        assert res.status_code == 422

    def test_search_grouped_response(self, sync_client: TestClient):
        """grouped=true returns categorized results."""
        res = sync_client.get("/api/search?q=a&grouped=true")
        assert res.status_code == 200
        data = res.json()
        assert "employees" in data
        assert "groups" in data
        assert "shifts" in data
        assert "leave_types" in data
        assert "results" not in data
        assert "query" in data

    def test_search_result_has_no_score(self, sync_client: TestClient):
        """Results should not expose internal score field."""
        res = sync_client.get("/api/search?q=a&limit=5")
        assert res.status_code == 200
        for r in res.json().get("results", []):
            assert "score" not in r
            assert "type" in r
            assert "id" in r
            assert "title" in r

    def test_search_grouped_no_score(self, sync_client: TestClient):
        """Grouped results should not expose internal score field."""
        res = sync_client.get("/api/search?q=a&grouped=true&limit=5")
        assert res.status_code == 200
        data = res.json()
        for category in ["employees", "groups", "shifts", "leave_types"]:
            for r in data.get(category, []):
                assert "score" not in r
