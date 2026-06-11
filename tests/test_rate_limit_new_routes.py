"""Phase 6: explizite slowapi-Limits auf den Phase-4/5-Routen.

POST /api/leave-entitlements/forfeit (Admin-Write) und GET
/api/personnel-table (teurer Report) bekommen wie ihre Geschwister-Routen
eigene Limits statt nur des globalen Defaults.
"""


def _limits_for(qualname: str) -> list[str]:
    from sp5api.dependencies import limiter

    return [str(lim.limit) for lim in limiter._route_limits.get(qualname, [])]


def test_forfeit_has_explicit_rate_limit(app):
    assert _limits_for("sp5api.routers.absences.forfeit_leave_rest") == [
        "5 per 1 minute"
    ]


def test_personnel_table_has_explicit_rate_limit(app):
    assert _limits_for("sp5api.routers.reports.get_personnel_table") == [
        "10 per 1 minute"
    ]


def test_routes_still_work(sync_client):
    # personnel-table beantwortet einen normalen Aufruf weiterhin mit 200
    resp = sync_client.get("/api/personnel-table?from=2024-12-01&to=2024-12-31")
    assert resp.status_code == 200
