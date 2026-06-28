"""Tests for create_absence (POST /api/absences) — the conflict/holiday
warning logic, the pending-status + planner-notification side effects, and the
overlap-conflict 409. Driven with a fake db; the status-file and notification
side effects are patched to no-ops."""

import secrets

from starlette.testclient import TestClient

import sp5api.routers.absences as absences


class _AbsDB:
    def __init__(self, *, schedule_day=None, holidays=None, add_exc=None,
                 groups=None, bans=None, note_exc=None):
        self._schedule_day = schedule_day or []
        self._holidays = holidays or []
        self._add_exc = add_exc
        self._groups = groups or []
        self._bans = bans or []
        self._note_exc = note_exc
        self.notes = []  # Mitschrift der add_note-Aufrufe (date, text, eid)

    def get_employee(self, eid):
        return {"ID": eid, "NAME": "Müller", "FIRSTNAME": "Anna"}

    def get_leave_type(self, ltid):
        return {"ID": ltid, "NAME": "Urlaub"}

    def get_schedule_day(self, date):
        return self._schedule_day

    def get_holiday_dates(self, year):
        return self._holidays

    def get_employee_groups(self, eid):
        return self._groups

    def get_holiday_bans(self, group_id=None):
        return self._bans

    def add_absence(self, eid, date, ltid, interval=0, start=0, end=0):
        # V-3: create_absence reicht interval/start/end als Kwargs durch
        if self._add_exc:
            raise self._add_exc
        return {"ID": 1, "INTERVAL": interval, "START": start, "END": end}

    def add_note(self, date, text, employee_id=0, text2="", category=""):
        if self._note_exc:
            raise self._note_exc
        self.notes.append((date, text, employee_id))
        return {"id": 1}

    def log_action(self, **kwargs):
        pass


def _planer_session():
    from sp5api.main import _sessions

    tok = secrets.token_hex(20)
    _sessions[tok] = {
        "ID": 910,
        "NAME": "abs_planer",
        "role": "Planer",
        "ADMIN": False,
        "RIGHTS": 2,
    }
    return tok


def _client(monkeypatch, db):
    from sp5api.main import app

    monkeypatch.setattr(absences, "get_db", lambda: db)
    monkeypatch.setattr(absences, "create_notification", lambda **kwargs: None)
    monkeypatch.setattr(absences, "_load_absence_status", lambda: {})
    monkeypatch.setattr(absences, "_save_absence_status", lambda data: None)
    return TestClient(app, raise_server_exceptions=False)


class TestCreateAbsence:
    def _post(self, client, tok, eid=5, date="2026-07-15", ltid=1):
        return client.post(
            "/api/absences",
            json={"employee_id": eid, "date": date, "leave_type_id": ltid},
            headers={"X-Auth-Token": tok},
        )

    def test_warns_about_existing_shift_and_holiday(self, monkeypatch):
        from sp5api.main import _sessions

        db = _AbsDB(
            schedule_day=[{"employee_id": 5, "kind": "shift", "shift_name": "Frühdienst"}],
            holidays=["2026-07-15"],
        )
        tok = _planer_session()
        try:
            resp = self._post(_client(monkeypatch, db), tok)
            assert resp.status_code == 200
            warnings = resp.json()["warnings"]
            assert any("Schicht" in w for w in warnings)
            assert any("Feiertag" in w for w in warnings)
        finally:
            _sessions.pop(tok, None)

    def test_comment_written_as_dienstplan_note(self, monkeypatch):
        # Lücke #5 (AbwesenheitenEintragen.09): Der optionale Kommentartext der
        # Abwesenheits-Eingabe landet als Dienstplan-Kommentar (5NOTE), HTML-
        # escaped, mit demselben Datum/MA — 5ABSEN selbst hat kein Textfeld.
        from sp5api.main import _sessions

        db = _AbsDB()
        tok = _planer_session()
        try:
            resp = _client(monkeypatch, db).post(
                "/api/absences",
                json={"employee_id": 5, "date": "2026-07-15", "leave_type_id": 1,
                      "comment": "Arzt <Reha>"},
                headers={"X-Auth-Token": tok},
            )
            assert resp.status_code == 200
            assert db.notes == [("2026-07-15", "Arzt &lt;Reha&gt;", 5)]
        finally:
            _sessions.pop(tok, None)

    def test_no_comment_writes_no_note(self, monkeypatch):
        from sp5api.main import _sessions

        db = _AbsDB()
        tok = _planer_session()
        try:
            resp = self._post(_client(monkeypatch, db), tok)
            assert resp.status_code == 200
            assert db.notes == []
        finally:
            _sessions.pop(tok, None)

    def test_blank_comment_writes_no_note(self, monkeypatch):
        from sp5api.main import _sessions

        db = _AbsDB()
        tok = _planer_session()
        try:
            resp = _client(monkeypatch, db).post(
                "/api/absences",
                json={"employee_id": 5, "date": "2026-07-15", "leave_type_id": 1,
                      "comment": "   "},
                headers={"X-Auth-Token": tok},
            )
            assert resp.status_code == 200
            assert db.notes == []
        finally:
            _sessions.pop(tok, None)

    def test_comment_failure_warns_but_keeps_absence(self, monkeypatch):
        # Notiz-Schreibfehler darf die Eintragung nicht blockieren (best-effort).
        from sp5api.main import _sessions

        db = _AbsDB(note_exc=RuntimeError("note boom"))
        tok = _planer_session()
        try:
            resp = _client(monkeypatch, db).post(
                "/api/absences",
                json={"employee_id": 5, "date": "2026-07-15", "leave_type_id": 1,
                      "comment": "wichtig"},
                headers={"X-Auth-Token": tok},
            )
            assert resp.status_code == 200
            assert any("Kommentar" in w for w in resp.json()["warnings"])
        finally:
            _sessions.pop(tok, None)

    def test_comment_too_long_rejected_422(self, monkeypatch):
        from sp5api.main import _sessions

        db = _AbsDB()
        tok = _planer_session()
        try:
            resp = _client(monkeypatch, db).post(
                "/api/absences",
                json={"employee_id": 5, "date": "2026-07-15", "leave_type_id": 1,
                      "comment": "x" * 126},
                headers={"X-Auth-Token": tok},
            )
            assert resp.status_code == 422
            assert db.notes == []
        finally:
            _sessions.pop(tok, None)

    def test_warns_about_holiday_ban(self, monkeypatch):
        # R5.10-5: Abwesenheit in einem Sperrzeitraum einer Gruppe des MA → Warnung.
        from sp5api.main import _sessions

        db = _AbsDB(
            groups=[51],
            bans=[{
                "id": 1, "group_id": 51, "group_name": "Team C",
                "start_date": "2026-07-10", "end_date": "2026-07-20",
                "restrict": 1, "reason": "Betriebsferien",
            }],
        )
        tok = _planer_session()
        try:
            resp = self._post(_client(monkeypatch, db), tok, date="2026-07-15")
            assert resp.status_code == 200
            warnings = resp.json()["warnings"]
            assert any("Urlaubssperre" in w and "Team C" in w for w in warnings), warnings
        finally:
            _sessions.pop(tok, None)

    def test_no_ban_warning_outside_period_or_group(self, monkeypatch):
        from sp5api.main import _sessions

        ban = {
            "id": 1, "group_id": 51, "group_name": "Team C",
            "start_date": "2026-07-10", "end_date": "2026-07-20",
            "restrict": 1, "reason": "Betriebsferien",
        }
        # (a) Datum außerhalb des Zeitraums; (b) MA nicht in der gesperrten Gruppe
        for groups, date in ([51], "2026-07-25"), ([99], "2026-07-15"):
            db = _AbsDB(groups=groups, bans=[ban])
            tok = _planer_session()
            try:
                resp = self._post(_client(monkeypatch, db), tok, date=date)
                assert resp.status_code == 200
                assert not any("Urlaubssperre" in w for w in resp.json()["warnings"])
            finally:
                _sessions.pop(tok, None)

    def test_overlap_conflict_returns_409(self, monkeypatch):
        from sp5api.main import _sessions

        db = _AbsDB(add_exc=ValueError("overlap"))
        tok = _planer_session()
        try:
            resp = self._post(_client(monkeypatch, db), tok)
            assert resp.status_code == 409
        finally:
            _sessions.pop(tok, None)

    def test_side_effect_failures_never_block_creation(self, monkeypatch):
        # Warning lookup, status-file write and notification all blow up, yet the
        # absence is still created (each is wrapped in a swallow-and-continue guard).
        from sp5api.main import _sessions, app

        class _BoomWarn(_AbsDB):
            def get_schedule_day(self, date):
                raise RuntimeError("warning lookup failed")

        monkeypatch.setattr(absences, "get_db", lambda: _BoomWarn())
        monkeypatch.setattr(absences, "_load_absence_status", lambda: {})
        monkeypatch.setattr(
            absences,
            "_save_absence_status",
            lambda data: (_ for _ in ()).throw(RuntimeError("status write failed")),
        )
        monkeypatch.setattr(
            absences,
            "create_notification",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("notify failed")),
        )
        tok = _planer_session()
        try:
            client = TestClient(app, raise_server_exceptions=False)
            client.headers["X-Auth-Token"] = tok
            resp = client.post(
                "/api/absences",
                json={"employee_id": 5, "date": "2026-07-15", "leave_type_id": 1},
            )
            assert resp.status_code == 200
        finally:
            _sessions.pop(tok, None)

    def test_unexpected_db_error_returns_sanitized_500(self, monkeypatch):
        from sp5api.main import _sessions

        db = _AbsDB(add_exc=RuntimeError("db boom"))
        tok = _planer_session()
        try:
            resp = self._post(_client(monkeypatch, db), tok)
            assert resp.status_code == 500
            assert "db boom" not in resp.text
        finally:
            _sessions.pop(tok, None)

    def test_employee_not_found_404(self, monkeypatch):
        from sp5api.main import _sessions

        class _NoEmp(_AbsDB):
            def get_employee(self, eid):
                return None

        tok = _planer_session()
        try:
            resp = self._post(_client(monkeypatch, _NoEmp()), tok)
            assert resp.status_code == 404
        finally:
            _sessions.pop(tok, None)

    def test_leave_type_not_found_404(self, monkeypatch):
        from sp5api.main import _sessions

        class _NoLt(_AbsDB):
            def get_leave_type(self, ltid):
                return None

        tok = _planer_session()
        try:
            resp = self._post(_client(monkeypatch, _NoLt()), tok)
            assert resp.status_code == 404
        finally:
            _sessions.pop(tok, None)


class _BulkDB:
    """Per-employee add_absence behaviour: 'ok' | 'skip' (ValueError) | 'error'."""

    def __init__(self, employees, behavior=None, leave_type=True):
        self._employees = employees
        self._behavior = behavior or {}
        self._leave_type = leave_type

    def get_leave_type(self, ltid):
        return {"ID": ltid, "NAME": "Urlaub"} if self._leave_type else None

    def get_employees(self, include_hidden=False):
        return self._employees

    def add_absence(self, eid, date, ltid, interval=0, start=0, end=0):
        # V-3: Signatur an die erweiterte lib-Fassade angepasst
        b = self._behavior.get(eid, "ok")
        if b == "skip":
            raise ValueError("already exists")
        if b == "error":
            raise RuntimeError("save boom")
        return {"ID": eid}


def _bulk_client(monkeypatch, db):
    from sp5api.main import app

    monkeypatch.setattr(absences, "get_db", lambda: db)
    monkeypatch.setattr(absences, "create_notification", lambda **kwargs: None)
    monkeypatch.setattr(absences, "_load_absence_status", lambda: {})
    monkeypatch.setattr(absences, "_save_absence_status", lambda data: None)
    return TestClient(app, raise_server_exceptions=False)


class TestBulkCreateAbsence:
    _URL = "/api/absences/bulk"

    def test_mixed_created_skipped_errors(self, monkeypatch):
        from sp5api.main import _sessions

        db = _BulkDB(
            [{"ID": 1}, {"ID": 2}, {"ID": 3}],
            behavior={1: "ok", 2: "skip", 3: "error"},
        )
        tok = _planer_session()
        try:
            client = _bulk_client(monkeypatch, db)
            client.headers["X-Auth-Token"] = tok
            resp = client.post(
                self._URL,
                json={"date": "2026-07-15", "leave_type_id": 1, "employee_ids": [1, 2, 3]},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["created"] == 1
            assert data["skipped"] == 1
            assert len(data["errors"]) == 1
        finally:
            _sessions.pop(tok, None)

    def test_all_active_when_no_ids(self, monkeypatch):
        from sp5api.main import _sessions

        db = _BulkDB([{"ID": 1}, {"ID": 2}])  # all default to "ok"
        tok = _planer_session()
        try:
            client = _bulk_client(monkeypatch, db)
            client.headers["X-Auth-Token"] = tok
            resp = client.post(self._URL, json={"date": "2026-07-15", "leave_type_id": 1})
            assert resp.status_code == 200
            assert resp.json()["created"] == 2  # both active employees
        finally:
            _sessions.pop(tok, None)

    def test_leave_type_not_found_404(self, monkeypatch):
        from sp5api.main import _sessions

        db = _BulkDB([{"ID": 1}], leave_type=False)
        tok = _planer_session()
        try:
            client = _bulk_client(monkeypatch, db)
            client.headers["X-Auth-Token"] = tok
            resp = client.post(
                self._URL, json={"date": "2026-07-15", "leave_type_id": 99, "employee_ids": [1]}
            )
            assert resp.status_code == 404
        finally:
            _sessions.pop(tok, None)
