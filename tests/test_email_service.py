"""Tests for sp5lib.email_service — SMTP email sending and notification bridge."""

import smtplib
from unittest.mock import MagicMock, patch

import pytest
from sp5lib.email_service import (
    EmailConfig,
    _render_html,
    _render_plain,
    get_config,
    send_email,
    send_email_async,
    send_notification_email,
)

# ── EmailConfig tests ─────────────────────────────────────────────────────────


class TestEmailConfig:
    """Test EmailConfig reads environment correctly."""

    def test_defaults_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without env vars, config is disabled."""
        for key in (
            "SP5_SMTP_HOST", "SP5_SMTP_PORT", "SP5_SMTP_USER",
            "SP5_SMTP_PASSWORD", "SP5_SMTP_FROM", "SP5_SMTP_TLS",
            "SP5_SMTP_ENABLED", "SP5_APP_URL",
        ):
            monkeypatch.delenv(key, raising=False)
        cfg = EmailConfig()
        assert cfg.host == ""
        assert cfg.port == 587
        assert cfg.user == ""
        assert cfg.password == ""
        assert cfg.from_addr == ""
        assert cfg.tls_mode == "true"
        assert cfg.enabled is False
        assert cfg.is_configured is False
        assert cfg.app_url == "http://localhost:8000"

    def test_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With host + user set, config is enabled."""
        monkeypatch.setenv("SP5_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SP5_SMTP_PORT", "465")
        monkeypatch.setenv("SP5_SMTP_USER", "noreply@example.com")
        monkeypatch.setenv("SP5_SMTP_PASSWORD", "secret123")
        monkeypatch.setenv("SP5_SMTP_TLS", "ssl")
        monkeypatch.setenv("SP5_APP_URL", "https://sp5.example.com")
        monkeypatch.delenv("SP5_SMTP_ENABLED", raising=False)
        cfg = EmailConfig()
        assert cfg.host == "smtp.example.com"
        assert cfg.port == 465
        assert cfg.user == "noreply@example.com"
        assert cfg.password == "secret123"
        assert cfg.from_addr == "noreply@example.com"
        assert cfg.tls_mode == "ssl"
        assert cfg.enabled is True
        assert cfg.is_configured is True
        assert cfg.app_url == "https://sp5.example.com"

    def test_from_addr_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SP5_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SP5_SMTP_USER", "user@example.com")
        monkeypatch.setenv("SP5_SMTP_FROM", "custom@example.com")
        monkeypatch.delenv("SP5_SMTP_ENABLED", raising=False)
        cfg = EmailConfig()
        assert cfg.from_addr == "custom@example.com"

    def test_explicit_disable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SP5_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SP5_SMTP_USER", "user@example.com")
        monkeypatch.setenv("SP5_SMTP_ENABLED", "false")
        cfg = EmailConfig()
        assert cfg.enabled is False
        assert cfg.is_configured is False

    def test_explicit_enable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SP5_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SP5_SMTP_ENABLED", "true")
        monkeypatch.delenv("SP5_SMTP_USER", raising=False)
        cfg = EmailConfig()
        assert cfg.enabled is True

    def test_to_safe_dict_no_password(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SP5_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SP5_SMTP_PASSWORD", "secret")
        monkeypatch.delenv("SP5_SMTP_ENABLED", raising=False)
        monkeypatch.delenv("SP5_SMTP_USER", raising=False)
        cfg = EmailConfig()
        d = cfg.to_safe_dict()
        assert "password" not in d
        assert d["host"] == "smtp.example.com"

    def test_app_url_trailing_slash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SP5_APP_URL", "https://sp5.example.com/")
        monkeypatch.delenv("SP5_SMTP_HOST", raising=False)
        monkeypatch.delenv("SP5_SMTP_ENABLED", raising=False)
        cfg = EmailConfig()
        assert cfg.app_url == "https://sp5.example.com"


class TestGetConfig:
    def test_returns_email_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SP5_SMTP_HOST", raising=False)
        monkeypatch.delenv("SP5_SMTP_ENABLED", raising=False)
        cfg = get_config()
        assert isinstance(cfg, EmailConfig)


# ── Template rendering ────────────────────────────────────────────────────────


class TestTemplateRendering:
    def test_render_html_basic(self) -> None:
        html = _render_html("Test Title", "Hello World")
        assert "Test Title" in html
        assert "Hello World" in html
        assert "OpenSchichtplaner5" in html

    def test_render_html_with_link(self) -> None:
        html = _render_html("Title", "Msg", link="/notifications", app_url="https://sp5.test")
        assert "https://sp5.test/notifications" in html
        assert "Jetzt ansehen" in html

    def test_render_html_absolute_link(self) -> None:
        html = _render_html("Title", "Msg", link="https://other.com/page")
        assert "https://other.com/page" in html

    def test_render_html_newlines(self) -> None:
        html = _render_html("Title", "Line1\nLine2")
        assert "Line1<br>Line2" in html

    def test_render_plain_basic(self) -> None:
        text = _render_plain("Test Title", "Hello World")
        assert "Test Title" in text
        assert "Hello World" in text

    def test_render_plain_with_link(self) -> None:
        text = _render_plain("Title", "Msg", link="/page", app_url="https://sp5.test")
        assert "https://sp5.test/page" in text
        assert "Link:" in text

    def test_render_plain_no_link(self) -> None:
        text = _render_plain("Title", "Msg")
        assert "Link:" not in text


# ── send_email ────────────────────────────────────────────────────────────────


class TestSendEmail:
    def test_skip_when_not_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SP5_SMTP_HOST", raising=False)
        monkeypatch.delenv("SP5_SMTP_ENABLED", raising=False)
        result = send_email(
            to="test@example.com", subject="Test", title="T", message="M"
        )
        assert result is False

    @patch("sp5lib.email_service.smtplib.SMTP")
    def test_send_starttls(
        self, mock_smtp_cls: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SP5_SMTP_HOST", "smtp.test.com")
        monkeypatch.setenv("SP5_SMTP_PORT", "587")
        monkeypatch.setenv("SP5_SMTP_USER", "user@test.com")
        monkeypatch.setenv("SP5_SMTP_PASSWORD", "pass")
        monkeypatch.setenv("SP5_SMTP_TLS", "true")
        monkeypatch.delenv("SP5_SMTP_ENABLED", raising=False)

        mock_srv = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_srv)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        result = send_email(
            to="recipient@example.com",
            subject="Test Subject",
            title="Test",
            message="Hello",
        )
        assert result is True
        mock_smtp_cls.assert_called_once_with("smtp.test.com", 587, timeout=15)
        mock_srv.starttls.assert_called_once()
        mock_srv.login.assert_called_once_with("user@test.com", "pass")
        mock_srv.send_message.assert_called_once()

    @patch("sp5lib.email_service.smtplib.SMTP_SSL")
    def test_send_ssl(
        self, mock_smtp_ssl_cls: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SP5_SMTP_HOST", "smtp.test.com")
        monkeypatch.setenv("SP5_SMTP_PORT", "465")
        monkeypatch.setenv("SP5_SMTP_USER", "user@test.com")
        monkeypatch.setenv("SP5_SMTP_PASSWORD", "pass")
        monkeypatch.setenv("SP5_SMTP_TLS", "ssl")
        monkeypatch.delenv("SP5_SMTP_ENABLED", raising=False)

        mock_srv = MagicMock()
        mock_smtp_ssl_cls.return_value.__enter__ = MagicMock(return_value=mock_srv)
        mock_smtp_ssl_cls.return_value.__exit__ = MagicMock(return_value=False)

        result = send_email(
            to="recipient@example.com",
            subject="Test",
            title="T",
            message="M",
        )
        assert result is True
        mock_smtp_ssl_cls.assert_called_once_with("smtp.test.com", 465, timeout=15)
        mock_srv.login.assert_called_once()
        mock_srv.send_message.assert_called_once()

    @patch("sp5lib.email_service.smtplib.SMTP")
    def test_send_no_auth(
        self, mock_smtp_cls: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SMTP without auth (no user set)."""
        monkeypatch.setenv("SP5_SMTP_HOST", "localhost")
        monkeypatch.setenv("SP5_SMTP_TLS", "false")
        monkeypatch.delenv("SP5_SMTP_USER", raising=False)
        monkeypatch.delenv("SP5_SMTP_PASSWORD", raising=False)
        monkeypatch.setenv("SP5_SMTP_ENABLED", "true")

        mock_srv = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_srv)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        result = send_email(
            to="test@example.com", subject="S", title="T", message="M"
        )
        assert result is True
        mock_srv.starttls.assert_not_called()
        mock_srv.login.assert_not_called()

    @patch("sp5lib.email_service.smtplib.SMTP")
    def test_send_failure(
        self, mock_smtp_cls: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SP5_SMTP_HOST", "smtp.test.com")
        monkeypatch.setenv("SP5_SMTP_USER", "user@test.com")
        monkeypatch.setenv("SP5_SMTP_PASSWORD", "pass")
        monkeypatch.delenv("SP5_SMTP_ENABLED", raising=False)

        mock_smtp_cls.side_effect = smtplib.SMTPException("Connection refused")

        result = send_email(
            to="test@example.com", subject="S", title="T", message="M"
        )
        assert result is False

    def test_send_with_explicit_config(self) -> None:
        """Pass config directly to skip env."""
        cfg = EmailConfig.__new__(EmailConfig)
        cfg.host = ""
        cfg.enabled = False
        result = send_email(
            to="test@example.com",
            subject="S",
            title="T",
            message="M",
            config=cfg,
        )
        assert result is False

    def test_send_with_link(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify link is included without errors."""
        monkeypatch.delenv("SP5_SMTP_HOST", raising=False)
        monkeypatch.delenv("SP5_SMTP_ENABLED", raising=False)
        # Will return False (not configured) but shouldn't raise
        result = send_email(
            to="test@example.com",
            subject="S",
            title="T",
            message="M",
            link="/notifications",
        )
        assert result is False


# ── send_email_async ──────────────────────────────────────────────────────────


class TestSendEmailAsync:
    @patch("sp5lib.email_service.send_email")
    def test_fires_thread(self, mock_send: MagicMock) -> None:
        mock_send.return_value = True
        send_email_async(
            to="test@example.com",
            subject="S",
            title="T",
            message="M",
        )
        # Give thread time to start
        import time
        time.sleep(0.1)
        mock_send.assert_called_once()


# ── send_notification_email ───────────────────────────────────────────────────


class TestSendNotificationEmail:
    def test_skip_no_email(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No recipient email → no send attempt."""
        monkeypatch.delenv("SP5_SMTP_HOST", raising=False)
        monkeypatch.delenv("SP5_SMTP_ENABLED", raising=False)
        # Should not raise
        send_notification_email(
            notification_type="general",
            title="Test",
            message="Hello",
            recipient_email=None,
        )

    @patch("sp5lib.email_service.send_email_async")
    def test_sends_with_type_prefix(
        self, mock_async: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SP5_SMTP_HOST", "smtp.test.com")
        monkeypatch.setenv("SP5_SMTP_USER", "noreply@test.com")
        monkeypatch.delenv("SP5_SMTP_ENABLED", raising=False)

        send_notification_email(
            notification_type="shift_change",
            title="Schicht geändert",
            message="Deine Schicht am Mo wurde geändert.",
            recipient_email="employee@test.com",
            link="/notifications",
        )
        mock_async.assert_called_once()
        call_kwargs = mock_async.call_args[1]
        assert call_kwargs["to"] == "employee@test.com"
        assert "[SP5] Schichtänderung:" in call_kwargs["subject"]
        assert call_kwargs["title"] == "Schicht geändert"

    @patch("sp5lib.email_service.send_email_async")
    def test_unknown_type_uses_default_prefix(
        self, mock_async: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SP5_SMTP_HOST", "smtp.test.com")
        monkeypatch.setenv("SP5_SMTP_USER", "noreply@test.com")
        monkeypatch.delenv("SP5_SMTP_ENABLED", raising=False)

        send_notification_email(
            notification_type="unknown_type",
            title="Something",
            message="Test",
            recipient_email="emp@test.com",
        )
        call_kwargs = mock_async.call_args[1]
        assert "[SP5] Benachrichtigung:" in call_kwargs["subject"]

    def test_skip_when_not_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SP5_SMTP_HOST", raising=False)
        monkeypatch.delenv("SP5_SMTP_ENABLED", raising=False)
        # Should not raise even with email
        send_notification_email(
            notification_type="general",
            title="Test",
            message="Hello",
            recipient_email="someone@test.com",
        )
