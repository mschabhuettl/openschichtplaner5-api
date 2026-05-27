"""Unit tests for _send_swap_email — the shift-swap email bridge in misc.py.
Like the other email bridges it must degrade silently: skip when SMTP is
unconfigured / the recipient is unknown / has no address, send otherwise, and
never let a failure propagate. Driven with a fake db + mocked email_service."""

from unittest.mock import MagicMock

import api.routers.misc as misc


class _DB:
    def __init__(self, employees):
        self._employees = employees

    def get_employees(self, include_hidden=True):
        return self._employees


def _setup(monkeypatch, *, configured=True, employees=None, send=None):
    from sp5lib import email_service

    monkeypatch.setattr(email_service, "get_config", lambda: MagicMock(is_configured=configured))
    monkeypatch.setattr(email_service, "send_notification_email", send or MagicMock())
    monkeypatch.setattr(misc, "get_db", lambda: _DB(employees or []))


def _call(**kwargs):
    defaults = dict(notification_type="swap", title="t", message="m", recipient_employee_id=5)
    defaults.update(kwargs)
    misc._send_swap_email(**defaults)


def test_skips_when_smtp_not_configured(monkeypatch):
    send = MagicMock()
    _setup(monkeypatch, configured=False, send=send)
    _call()
    send.assert_not_called()


def test_skips_when_employee_not_found(monkeypatch):
    send = MagicMock()
    _setup(monkeypatch, employees=[{"ID": 99, "EMAIL": "x@y.de"}], send=send)
    _call(recipient_employee_id=5)
    send.assert_not_called()


def test_skips_when_employee_has_no_email(monkeypatch):
    send = MagicMock()
    _setup(monkeypatch, employees=[{"ID": 5, "EMAIL": ""}], send=send)
    _call(recipient_employee_id=5)
    send.assert_not_called()


def test_sends_to_employee_email(monkeypatch):
    send = MagicMock()
    _setup(monkeypatch, employees=[{"ID": 5, "EMAIL": "a@b.de"}], send=send)
    _call(recipient_employee_id=5, link="/tauschboerse")
    send.assert_called_once()
    assert send.call_args.kwargs["recipient_email"] == "a@b.de"


def test_swallows_exceptions(monkeypatch):
    from sp5lib import email_service

    monkeypatch.setattr(
        email_service, "get_config", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    # Must not raise even though get_config blows up.
    misc._send_swap_email(notification_type="swap", title="t", message="m", recipient_employee_id=5)
