"""
Performance test: assert key endpoints respond in < 2s.

Uses the shared sync_client fixture (admin role, real DBF data via mtime cache).
"""
import time
import pytest


ENDPOINTS = [
    "/api/employees",
    "/api/schedule?year=2026&month=2",
    "/api/groups",
    "/api/dashboard/summary",
    "/api/schedule/conflicts?year=2026&month=2",
]

MAX_RESPONSE_TIME_S = 2.0


@pytest.mark.parametrize("path", ENDPOINTS)
def test_response_time(sync_client, path):
    """Each endpoint must respond within MAX_RESPONSE_TIME_S seconds."""
    start = time.perf_counter()
    response = sync_client.get(path)
    elapsed = time.perf_counter() - start

    assert response.status_code in (200, 204), (
        f"Unexpected status {response.status_code} for {path}"
    )
    assert elapsed < MAX_RESPONSE_TIME_S, (
        f"Endpoint {path} took {elapsed:.3f}s (limit {MAX_RESPONSE_TIME_S}s)"
    )
