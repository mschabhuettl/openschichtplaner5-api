"""SP5_CORE_ONLY: Instanz im Original-Funktionsumfang.

EXTRA-Bereiche (docs/feature-classification.md des App-Repos) antworten 404
mit klarer Meldung — jede Methode, auch GET. CORE-Bereiche bleiben voll
nutzbar. Default (Flag aus) verhält sich unverändert.
"""

import pytest
from starlette.testclient import TestClient

import sp5api.main as main_module


@pytest.fixture
def core_only_on(monkeypatch):
    monkeypatch.setattr(main_module, "_CORE_ONLY", True)


EXTRA_SAMPLES = [
    ("GET", "/api/swap-requests"),
    ("POST", "/api/swap-requests"),
    ("GET", "/api/wishes"),
    ("GET", "/api/self/schedule"),
    ("GET", "/api/fairness"),
    ("GET", "/api/changelog"),
    ("GET", "/api/companies"),
    ("GET", "/api/webhooks"),
    ("GET", "/api/scheduled-reports"),
    ("GET", "/api/ical/my-schedule.ics"),
    ("GET", "/api/work-time-rules"),
    ("GET", "/api/shifts/recurring"),
    ("GET", "/api/v1/wishes"),  # v1-Präfix wird mit abgedeckt
]

CORE_SAMPLES = [
    ("GET", "/api/employees"),
    ("GET", "/api/shifts"),
    ("GET", "/api/groups"),
    ("GET", "/api/holidays"),
    ("GET", "/api/schedule?year=2026&month=7"),
    ("GET", "/api/leave-types"),
    ("GET", "/api/restrictions"),
    ("GET", "/api/notes"),
]


class TestCoreOnlyEnforced:
    @pytest.mark.parametrize("method,path", EXTRA_SAMPLES)
    def test_extra_endpoints_disabled(self, admin_client: TestClient, core_only_on, method, path):
        res = admin_client.request(method, path)
        assert res.status_code == 404, (path, res.status_code)
        assert "Core-Modus" in res.text

    @pytest.mark.parametrize("method,path", CORE_SAMPLES)
    def test_core_endpoints_available(self, admin_client: TestClient, core_only_on, method, path):
        res = admin_client.request(method, path)
        assert res.status_code == 200, (path, res.status_code)

    def test_health_reports_flag(self, sync_client: TestClient, core_only_on):
        res = sync_client.get("/api/health")
        assert res.json()["core_only"] is True


class TestCoreOnlyDefaultOff:
    def test_default_extra_available(self, admin_client: TestClient):
        assert main_module._CORE_ONLY is False
        res = admin_client.get("/api/wishes")
        assert res.status_code == 200
        res2 = admin_client.get("/api/swap-requests")
        assert res2.status_code == 200

    def test_health_reports_flag_off(self, sync_client: TestClient):
        assert sync_client.get("/api/health").json()["core_only"] is False
