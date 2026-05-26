"""Internals of api/routers/notifications.py — storage error-swallowing and
the email bridge (_try_send_email). These paths never run in the happy-path
endpoint tests, so they're exercised directly here."""

from unittest.mock import MagicMock, patch


class TestNotificationStorage:
    """_load / _save must never raise on a bad file."""

    def test_load_returns_empty_on_corrupt_file(self, tmp_path):
        import api.routers.notifications as notif

        bad = tmp_path / "notif.json"
        bad.write_text("not json{", encoding="utf-8")
        with patch.object(notif, "_NOTIF_FILE", str(bad)):
            assert notif._load() == []

    def test_save_swallows_write_errors(self):
        import api.routers.notifications as notif

        # The target directory does not exist → the temp-file write fails and
        # must be swallowed rather than propagating.
        with patch.object(notif, "_NOTIF_FILE", "/nonexistent-dir-xyz/notif.json"):
            notif._save([{"id": 1}])  # must not raise


class TestEmailBridge:
    """_try_send_email resolves the recipient and sends, tolerating failures."""

    def _patches(self, *, configured=True, employee=None, send=None):
        from api.routers import reports
        from sp5lib import email_service

        cfg = MagicMock(is_configured=configured)
        db = MagicMock()
        db.get_employee.return_value = employee
        return [
            patch.object(email_service, "get_config", return_value=cfg),
            patch.object(email_service, "send_notification_email", send or MagicMock()),
            patch.object(reports, "get_db", return_value=db),
        ]

    def _run(self, patches, **kwargs):
        import api.routers.notifications as notif

        defaults = dict(
            notification_type="info",
            title="t",
            message="m",
            recipient_employee_id=5,
            link=None,
        )
        defaults.update(kwargs)
        for p in patches:
            p.start()
        try:
            notif._try_send_email(**defaults)
        finally:
            for p in patches:
                p.stop()

    def test_skips_when_email_not_configured(self):
        send = MagicMock()
        self._run(self._patches(configured=False, send=send))
        send.assert_not_called()

    def test_skips_planner_wide_notifications(self):
        # recipient_employee_id=None → in-app only, no email lookup/send
        send = MagicMock()
        self._run(self._patches(configured=True, send=send), recipient_employee_id=None)
        send.assert_not_called()

    def test_sends_to_resolved_employee_email(self):
        send = MagicMock()
        self._run(
            self._patches(configured=True, employee={"EMAIL": "a@b.de"}, send=send),
            recipient_employee_id=5,
            link="/x",
        )
        send.assert_called_once()
        assert send.call_args.kwargs["recipient_email"] == "a@b.de"

    def test_skips_when_employee_has_no_email(self):
        send = MagicMock()
        self._run(
            self._patches(configured=True, employee={"EMAIL": ""}, send=send),
            recipient_employee_id=5,
        )
        send.assert_not_called()

    def test_skips_when_employee_not_found(self):
        send = MagicMock()
        self._run(
            self._patches(configured=True, employee=None, send=send),
            recipient_employee_id=999,
        )
        send.assert_not_called()

    def test_swallows_exceptions(self):
        import api.routers.notifications as notif
        from sp5lib import email_service

        # get_config blowing up must not propagate out of the bridge.
        with patch.object(email_service, "get_config", side_effect=RuntimeError("boom")):
            notif._try_send_email(
                notification_type="info",
                title="t",
                message="m",
                recipient_employee_id=5,
                link=None,
            )
