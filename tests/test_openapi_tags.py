"""Guard: every OpenAPI tag used by a route is described in _OPENAPI_TAGS.

Without this, new routers can introduce tag groups that render in the Swagger UI
with no description (as happened for Reports/Notifications/ORM Mirror/… before).
"""

import api.main as main


def _route_tags() -> set[str]:
    tags: set[str] = set()
    for route in main.app.routes:
        for t in getattr(route, "tags", []) or []:
            tags.add(t)
    return tags


def test_every_route_tag_is_described():
    described = {t["name"] for t in main._OPENAPI_TAGS}
    used = _route_tags()
    undescribed = used - described
    assert not undescribed, (
        f"OpenAPI tags missing a description in _OPENAPI_TAGS: {sorted(undescribed)}"
    )


def test_no_duplicate_tag_descriptions():
    names = [t["name"] for t in main._OPENAPI_TAGS]
    assert len(names) == len(set(names)), "duplicate tag entries in _OPENAPI_TAGS"
