"""Coverage boost for export_scheduler.py — targets _generate_export and _send_export_email.

The CRUD endpoints are already well tested; this covers the helper functions
that make up the bulk of the uncovered lines (45% → 80%+).
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


class TestGenerateExport:
    """Tests for _generate_export (CSV and XLSX paths)."""

    @pytest.fixture(autouse=True)
    def _mock_db(self):
        """Mock sp5lib.db.get_db to return predictable schedule/employee data."""
        mock_db = MagicMock()
        mock_db.get_schedule.return_value = [
            {
                "employee_id": "EMP1",
                "date": "2025-01-15",
                "display_name": "Früh",
                "color_bk": "#4A90D9",
                "color_text": "#FFFFFF",
            },
        ]
        mock_db.get_employees.return_value = [
            {
                "ID": "EMP1", "NAME": "Müller", "FIRSTNAME": "Hans",
                "SHORTNAME": "HM", "POSITION": 1, "BOLD": False,
                "CBKLABEL": 0, "CBKLABEL_HEX": "#f8fafc", "CFGLABEL_HEX": "#000000",
            },
            {
                "ID": "EMP2", "NAME": "Schmidt", "FIRSTNAME": "Anna",
                "SHORTNAME": "AS", "POSITION": 2, "BOLD": True,
                "CBKLABEL": 255, "CBKLABEL_HEX": "#0000ff", "CFGLABEL_HEX": "#ffffff",
            },
        ]
        mock_db.get_group_members.return_value = ["EMP1"]
        self.mock_db = mock_db

        # Patch at the sp5lib.db module level
        fake_db_module = MagicMock()
        fake_db_module.get_db = MagicMock(return_value=mock_db)
        with patch.dict("sys.modules", {"sp5lib.db": fake_db_module}):
            yield

    def test_csv_export_all_groups(self):
        from api.routers.export_scheduler import _generate_export
        file_bytes, row_count = _generate_export("csv", None, "2025-01")
        assert row_count == 2
        content = file_bytes.decode("utf-8-sig")
        assert "Müller" in content
        assert "Schmidt" in content
        assert "Mitarbeiter" in content

    def test_csv_export_filtered_group(self):
        from api.routers.export_scheduler import _generate_export
        file_bytes, row_count = _generate_export("csv", 1, "2025-01")
        assert row_count == 1
        content = file_bytes.decode("utf-8-sig")
        assert "Müller" in content
        assert "Schmidt" not in content

    def test_xlsx_export(self):
        from api.routers.export_scheduler import _generate_export
        file_bytes, row_count = _generate_export("xlsx", None, "2025-01")
        assert row_count == 2
        assert file_bytes[:2] == b"PK"  # zip/xlsx magic bytes
        assert len(file_bytes) > 100

    def test_xlsx_export_with_group_filter(self):
        from api.routers.export_scheduler import _generate_export
        file_bytes, row_count = _generate_export("xlsx", 1, "2025-01")
        assert row_count == 1

    def test_invalid_month_format(self):
        from api.routers.export_scheduler import _generate_export
        with pytest.raises(ValueError, match="Invalid month format"):
            _generate_export("csv", None, "not-a-month")

    def test_february_leap_year(self):
        from api.routers.export_scheduler import _generate_export
        file_bytes, row_count = _generate_export("csv", None, "2024-02")
        content = file_bytes.decode("utf-8-sig")
        assert "29" in content  # Feb 2024 has 29 days


class TestSendExportEmail:
    """Tests for _send_export_email."""

    def test_smtp_not_configured(self):
        from api.routers.export_scheduler import _send_export_email

        class FakeConfig:
            is_configured = False

        with patch("sp5lib.email_service.get_config", return_value=FakeConfig()):
            result = _send_export_email(
                {"name": "Test", "email_to": ["a@b.com"]},
                b"data", 5, "2025-01", "xlsx",
            )
        assert result["success"] is False
        assert "SMTP" in result["reason"]
        assert "export_url" in result

    def test_all_sends_fail(self):
        from api.routers.export_scheduler import _send_export_email

        class FakeConfig:
            is_configured = True
            host = "localhost"
            port = 587
            user = "user"
            password = "pass"
            from_addr = "noreply@test.com"
            tls_mode = "true"

        with patch("sp5lib.email_service.get_config", return_value=FakeConfig()):
            with patch("smtplib.SMTP", side_effect=ConnectionRefusedError("no server")):
                result = _send_export_email(
                    {"name": "Test", "email_to": ["a@b.com", "c@d.com"]},
                    b"data", 5, "2025-01", "xlsx",
                )
        assert result["success"] is False
        assert "All email sends failed" in result["reason"]
        assert len(result["failed"]) == 2

    def test_partial_send_success(self):
        from api.routers.export_scheduler import _send_export_email

        class FakeConfig:
            is_configured = True
            host = "localhost"
            port = 587
            user = "user"
            password = "pass"
            from_addr = "noreply@test.com"
            tls_mode = "true"

        call_count = 0

        class FakeSMTP:
            def __init__(self, *args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count > 1:
                    raise ConnectionRefusedError("fail")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def starttls(self):
                pass

            def login(self, *args):
                pass

            def send_message(self, msg):
                pass

        with patch("sp5lib.email_service.get_config", return_value=FakeConfig()):
            with patch("smtplib.SMTP", FakeSMTP):
                result = _send_export_email(
                    {"name": "Test", "email_to": ["ok@test.com", "fail@test.com"]},
                    b"data", 5, "2025-01", "csv",
                )
        assert result["success"] is True
        assert "ok@test.com" in result["sent_to"]
        assert "fail@test.com" in result["failed"]

    def test_ssl_mode(self):
        from api.routers.export_scheduler import _send_export_email

        class FakeConfig:
            is_configured = True
            host = "localhost"
            port = 465
            user = ""
            password = ""
            from_addr = "noreply@test.com"
            tls_mode = "ssl"

        class FakeSMTPSSL:
            def __init__(self, *args, **kwargs):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def send_message(self, msg):
                pass

        with patch("sp5lib.email_service.get_config", return_value=FakeConfig()):
            with patch("smtplib.SMTP_SSL", FakeSMTPSSL):
                result = _send_export_email(
                    {"name": "SSL Test", "email_to": ["a@b.com"]},
                    b"data", 3, "2025-03", "xlsx",
                )
        assert result["success"] is True


class TestScheduleValidation:
    """Test Pydantic model validation for edge cases."""

    def test_schedule_create_valid(self):
        from api.routers.export_scheduler import ScheduleCreate
        s = ScheduleCreate(
            name="Test", day_of_week=0, time="08:00",
            format="xlsx", email_to=["a@b.com"],
        )
        assert s.frequency == "weekly"

    def test_schedule_update_partial(self):
        from api.routers.export_scheduler import ScheduleUpdate
        s = ScheduleUpdate(name="New name")
        assert s.format is None
        assert s.email_to is None

    def test_schedule_update_validate_time(self):
        from api.routers.export_scheduler import ScheduleUpdate
        with pytest.raises(ValidationError):
            ScheduleUpdate(time="99:99")

    def test_schedule_update_validate_format(self):
        from api.routers.export_scheduler import ScheduleUpdate
        with pytest.raises(ValidationError):
            ScheduleUpdate(format="pdf")

    def test_schedule_update_validate_frequency(self):
        from api.routers.export_scheduler import ScheduleUpdate
        with pytest.raises(ValidationError):
            ScheduleUpdate(frequency="daily")

    def test_schedule_update_validate_empty_emails(self):
        from api.routers.export_scheduler import ScheduleUpdate
        with pytest.raises(ValidationError):
            ScheduleUpdate(email_to=[])

    def test_schedule_update_validate_bad_email(self):
        from api.routers.export_scheduler import ScheduleUpdate
        with pytest.raises(ValidationError):
            ScheduleUpdate(email_to=["no-at-sign"])
