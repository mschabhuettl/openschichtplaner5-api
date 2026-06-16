"""Cycle 8 regression: write paths must never end in an opaque 500.

The maintainer's three reported 500s (reschedule, wishes, new employee) were all
``PermissionError`` (EACCES/EROFS) writing the mounted DBF data directory from the
non-root container. Two guarantees are tested here:

  * ``describe_write_error`` maps filesystem errors to a clear status + message;
  * a write that raises ``PermissionError`` returns a clear 5xx with a specific
    message (not the generic "Interner Serverfehler"), covering BOTH the path that
    wraps its DB call (employees → ``_sanitize_500``) and the one that does not
    (wishes → global ``OSError`` handler).
"""

import errno

from sp5api.dependencies import describe_write_error


def test_describe_write_error_mapping():
    assert describe_write_error(
        PermissionError(errno.EACCES, "Permission denied", "/app/data/5EMPL.DBF")
    )[0] == 503
    assert describe_write_error(OSError(errno.EROFS, "Read-only file system"))[0] == 503
    assert describe_write_error(OSError(errno.ENOSPC, "No space left"))[0] == 507
    assert describe_write_error(ValueError("not a filesystem error")) is None
    detail = describe_write_error(PermissionError(errno.EACCES, "Permission denied"))[1]
    assert "nicht beschreibbar" in detail


def test_employee_write_permission_error_is_clear(write_client, monkeypatch):
    """create_employee wraps its DB call → _sanitize_500 must yield a clear 503."""
    from sp5lib.database import SP5Database

    def boom(self, data):
        raise PermissionError(errno.EACCES, "Permission denied", "/app/data/5EMPL.DBF")

    monkeypatch.setattr(SP5Database, "create_employee", boom)
    r = write_client.post("/api/employees", json={"NAME": "X", "SHORTNAME": "ZZXP"})
    assert r.status_code == 503, r.text
    assert "nicht beschreibbar" in r.json()["detail"]
    assert "Interner Serverfehler" not in r.json()["detail"]


def test_wish_write_permission_error_is_clear(write_client, monkeypatch):
    """create_wish does NOT wrap its DB call → the global OSError handler must
    still turn the PermissionError into a clear 503, not an opaque 500."""
    from sp5lib.database import SP5Database

    def boom(self, **kwargs):
        raise PermissionError(errno.EACCES, "Permission denied", "/app/data/wishes.json")

    monkeypatch.setattr(SP5Database, "add_wish", boom)
    r = write_client.post(
        "/api/wishes",
        json={"employee_id": 1, "date": "2026-06-20", "wish_type": "WUNSCH"},
    )
    assert r.status_code == 503, r.text
    assert "nicht beschreibbar" in r.json()["detail"]
