"""Tests for schedule comments endpoint (Q069)."""

from starlette.testclient import TestClient

# ── Backend: Schedule Comments ─────────────────────────────────────────────────


class TestScheduleCommentsRead:
    """Read access for all authenticated users."""

    def test_list_comments_empty(self, write_client):
        """GET /api/v1/schedule/comments returns empty list by default."""
        resp = write_client.get("/api/v1/schedule/comments")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_list_comments_unauthenticated(self, app):
        """Unauthenticated access returns 401."""
        with TestClient(app) as c:
            resp = c.get("/api/v1/schedule/comments")
        assert resp.status_code == 401

    def test_leser_can_read(self, leser_client):
        """Leser role can list comments."""
        resp = leser_client.get("/api/v1/schedule/comments")
        assert resp.status_code == 200

    def test_list_comments_by_group(self, write_client):
        """Filter comments by group_id."""
        # Create a comment first
        write_client.post(
            "/api/v1/schedule/comments",
            json={
                "date": "2025-01-15",
                "group_id": 42,
                "text": "Group 42 note",
            },
        )
        resp = write_client.get("/api/v1/schedule/comments?group_id=42")
        assert resp.status_code == 200
        data = resp.json()
        assert any(c["group_id"] == 42 for c in data)

    def test_list_comments_by_date_range(self, write_client):
        """Filter comments by from/to date range."""
        write_client.post(
            "/api/v1/schedule/comments",
            json={
                "date": "2025-03-10",
                "group_id": 0,
                "text": "March comment",
            },
        )
        resp = write_client.get("/api/v1/schedule/comments?from=2025-03-01&to=2025-03-31")
        assert resp.status_code == 200
        data = resp.json()
        for c in data:
            assert "2025-03-01" <= c["date"] <= "2025-03-31"


class TestScheduleCommentsCreate:
    """Create comments (Planer/Admin only)."""

    def test_create_comment(self, write_client):
        """POST creates a comment and returns it."""
        resp = write_client.post(
            "/api/v1/schedule/comments",
            json={
                "date": "2025-06-15",
                "group_id": 1,
                "text": "Team A meeting day",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["date"] == "2025-06-15"
        assert data["group_id"] == 1
        assert data["text"] == "Team A meeting day"
        assert "id" in data
        assert isinstance(data["id"], int)

    def test_create_comment_replaces_existing(self, write_client):
        """Creating a second comment for same date+group replaces the first."""
        write_client.post(
            "/api/v1/schedule/comments",
            json={
                "date": "2025-07-01",
                "group_id": 5,
                "text": "First note",
            },
        )
        write_client.post(
            "/api/v1/schedule/comments",
            json={
                "date": "2025-07-01",
                "group_id": 5,
                "text": "Updated note",
            },
        )
        resp = write_client.get(
            "/api/v1/schedule/comments?group_id=5&from=2025-07-01&to=2025-07-01"
        )
        assert resp.status_code == 200
        data = resp.json()
        # Should be exactly one comment for this date+group
        matching = [c for c in data if c["date"] == "2025-07-01" and c["group_id"] == 5]
        assert len(matching) == 1
        assert matching[0]["text"] == "Updated note"

    def test_leser_cannot_create(self, leser_client):
        """Leser role cannot create comments."""
        resp = leser_client.post(
            "/api/v1/schedule/comments",
            json={
                "date": "2025-08-01",
                "group_id": 0,
                "text": "Should fail",
            },
        )
        assert resp.status_code == 403

    def test_create_invalid_date(self, write_client):
        """POST with invalid date returns 400."""
        resp = write_client.post(
            "/api/v1/schedule/comments",
            json={
                "date": "not-a-date",
                "group_id": 0,
                "text": "Bad date",
            },
        )
        assert resp.status_code in (400, 422)

    def test_create_empty_text_rejected(self, write_client):
        """Empty text is rejected."""
        resp = write_client.post(
            "/api/v1/schedule/comments",
            json={
                "date": "2025-09-01",
                "group_id": 0,
                "text": "",
            },
        )
        assert resp.status_code == 422

    def test_comment_has_author(self, write_client):
        """Created comment includes author field."""
        resp = write_client.post(
            "/api/v1/schedule/comments",
            json={
                "date": "2025-10-20",
                "group_id": 3,
                "text": "Author test",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "author" in data

    def test_comment_has_created_at(self, write_client):
        """Created comment includes created_at timestamp."""
        resp = write_client.post(
            "/api/v1/schedule/comments",
            json={
                "date": "2025-11-05",
                "group_id": 2,
                "text": "Timestamp test",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "created_at" in data


class TestScheduleCommentsDelete:
    """Delete comments (Planer/Admin only)."""

    def test_delete_comment(self, write_client):
        """DELETE removes the comment."""
        # Create
        create_resp = write_client.post(
            "/api/v1/schedule/comments",
            json={
                "date": "2025-12-24",
                "group_id": 0,
                "text": "Christmas note",
            },
        )
        assert create_resp.status_code == 201
        comment_id = create_resp.json()["id"]

        # Delete
        del_resp = write_client.delete(f"/api/v1/schedule/comments/{comment_id}")
        assert del_resp.status_code == 200
        assert del_resp.json()["ok"] is True

        # Verify gone
        list_resp = write_client.get("/api/v1/schedule/comments?from=2025-12-24&to=2025-12-24")
        data = list_resp.json()
        assert not any(c["id"] == comment_id for c in data)

    def test_delete_nonexistent_returns_404(self, write_client):
        """Deleting a comment that doesn't exist returns 404."""
        resp = write_client.delete("/api/v1/schedule/comments/999999")
        assert resp.status_code == 404

    def test_leser_cannot_delete(self, leser_client, write_client):
        """Leser role cannot delete comments."""
        # Create as admin
        create_resp = write_client.post(
            "/api/v1/schedule/comments",
            json={
                "date": "2025-08-10",
                "group_id": 0,
                "text": "Protected note",
            },
        )
        assert create_resp.status_code == 201
        comment_id = create_resp.json()["id"]

        # Try delete as Leser
        del_resp = leser_client.delete(f"/api/v1/schedule/comments/{comment_id}")
        assert del_resp.status_code == 403

    def test_planer_can_delete(self, planer_client, write_client):
        """Planer role can delete comments."""
        create_resp = write_client.post(
            "/api/v1/schedule/comments",
            json={
                "date": "2025-09-15",
                "group_id": 0,
                "text": "Planer delete test",
            },
        )
        assert create_resp.status_code == 201
        comment_id = create_resp.json()["id"]

        del_resp = planer_client.delete(f"/api/v1/schedule/comments/{comment_id}")
        assert del_resp.status_code == 200


class TestScheduleCommentsErrorPaths:
    """Validation and DB-failure branches return clean, sanitized errors."""

    def test_create_invalid_calendar_date(self, write_client):
        """A date that matches the pattern but isn't a real date returns 400."""
        # "2025-13-45" passes the YYYY-MM-DD regex but fails strptime.
        resp = write_client.post(
            "/api/v1/schedule/comments",
            json={
                "date": "2025-13-45",
                "group_id": 0,
                "text": "Impossible date",
            },
        )
        assert resp.status_code == 400
        assert "YYYY-MM-DD" in resp.json()["detail"]

    def test_list_db_error_is_sanitized_500(self, write_client, monkeypatch):
        """A DB failure on list returns a sanitized 500 (no internals leaked)."""
        monkeypatch.setattr("api.routers.schedule_comments.get_db", lambda: _BoomDB())
        resp = write_client.get("/api/v1/schedule/comments")
        assert resp.status_code == 500
        assert resp.json()["detail"] == "Interner Serverfehler. Bitte versuche es erneut."
        assert "boom" not in resp.text  # raw error not exposed

    def test_create_db_error_is_sanitized_500(self, write_client, monkeypatch):
        """A DB failure on create returns a sanitized 500."""
        monkeypatch.setattr("api.routers.schedule_comments.get_db", lambda: _BoomDB())
        resp = write_client.post(
            "/api/v1/schedule/comments",
            json={
                "date": "2025-06-15",
                "group_id": 1,
                "text": "triggers db error",
            },
        )
        assert resp.status_code == 500
        assert resp.json()["detail"] == "Interner Serverfehler. Bitte versuche es erneut."

    def test_delete_db_error_is_sanitized_500(self, write_client, monkeypatch):
        """A non-HTTP DB failure on delete returns a sanitized 500."""
        monkeypatch.setattr("api.routers.schedule_comments.get_db", lambda: _BoomDB())
        resp = write_client.delete("/api/v1/schedule/comments/123")
        assert resp.status_code == 500
        assert resp.json()["detail"] == "Interner Serverfehler. Bitte versuche es erneut."


class _BoomDB:
    """Fake DB whose comment methods always raise, to exercise error handlers."""

    def get_schedule_comments(self, **kwargs):
        raise RuntimeError("boom")

    def add_schedule_comment(self, **kwargs):
        raise RuntimeError("boom")

    def delete_schedule_comment(self, comment_id):
        raise RuntimeError("boom")


class TestScheduleCommentsDatabase:
    """Unit tests for the database methods directly."""

    def test_db_add_and_get(self, write_db_path):
        """Database add_schedule_comment and get_schedule_comments work."""
        from sp5lib.database import SP5Database

        db = SP5Database(write_db_path)

        comment = db.add_schedule_comment(
            date="2025-05-01",
            group_id=10,
            text="Labor Day",
            author="admin",
        )
        assert comment["id"] >= 1
        assert comment["text"] == "Labor Day"

        results = db.get_schedule_comments(group_id=10)
        assert any(c["id"] == comment["id"] for c in results)

    def test_db_one_per_day_group(self, write_db_path):
        """Adding a second comment for same date+group replaces the first."""
        from sp5lib.database import SP5Database

        db = SP5Database(write_db_path)

        db.add_schedule_comment(date="2025-05-02", group_id=7, text="First", author="a")
        db.add_schedule_comment(date="2025-05-02", group_id=7, text="Second", author="b")

        results = db.get_schedule_comments(group_id=7, date_from="2025-05-02", date_to="2025-05-02")
        assert len(results) == 1
        assert results[0]["text"] == "Second"

    def test_db_delete(self, write_db_path):
        """Database delete_schedule_comment removes entry."""
        from sp5lib.database import SP5Database

        db = SP5Database(write_db_path)

        comment = db.add_schedule_comment(
            date="2025-06-01",
            group_id=0,
            text="To delete",
        )
        deleted = db.delete_schedule_comment(comment["id"])
        assert deleted == 1

        results = db.get_schedule_comments()
        assert not any(c["id"] == comment["id"] for c in results)

    def test_db_delete_nonexistent(self, write_db_path):
        """Deleting non-existent comment returns 0."""
        from sp5lib.database import SP5Database

        db = SP5Database(write_db_path)

        deleted = db.delete_schedule_comment(99999)
        assert deleted == 0
