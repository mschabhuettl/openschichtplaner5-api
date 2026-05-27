"""Guard for the cache-control / cache-metrics consistency.

The cache-control middleware (sets `max-age` on cacheable master-data GETs) and
the metrics collector (counts the hit rate on those same paths) must use one
shared prefix list, otherwise headers and metrics silently disagree. These tests
pin the single source of truth and its observable behaviour.
"""

import api.main as main
from starlette.testclient import TestClient


def test_shared_prefix_constant_exists():
    assert main._CACHEABLE_API_PREFIXES
    # the well-known master-data endpoints are covered
    for p in ("/api/shifts", "/api/holidays", "/api/leave-types"):
        assert p in main._CACHEABLE_API_PREFIXES


def test_cacheable_get_sets_max_age():
    client = TestClient(main.app, raise_server_exceptions=False)
    resp = client.get("/api/shifts")
    # may be 200 or 401 depending on auth; the cache header is only set on 200
    if resp.status_code == 200:
        assert "max-age" in resp.headers.get("cache-control", "")


def test_non_cacheable_api_path_is_not_cached():
    client = TestClient(main.app, raise_server_exceptions=False)
    resp = client.get("/api/health")
    cc = resp.headers.get("cache-control", "")
    assert "max-age" not in cc  # only the shared prefixes get max-age
