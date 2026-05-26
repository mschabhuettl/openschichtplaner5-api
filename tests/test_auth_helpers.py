"""Tests for auth password-strength validation and the change-password reject path.

_validate_password_strength is security-relevant and was only exercised indirectly;
test all three rejection branches + the success path directly, plus the
change-own-password wrong-old-password 403.
"""

import pytest
from api.routers.auth import _validate_password_strength
from fastapi import HTTPException


class TestValidatePasswordStrength:
    def test_valid_password_passes(self):
        # 9 chars, uppercase, digit → no exception
        _validate_password_strength("Passwort1")

    def test_too_short_rejected(self):
        with pytest.raises(HTTPException) as exc:
            _validate_password_strength("Pw1")
        assert exc.value.status_code == 400

    def test_missing_uppercase_rejected(self):
        with pytest.raises(HTTPException) as exc:
            _validate_password_strength("passwort1")
        assert exc.value.status_code == 400

    def test_missing_digit_rejected(self):
        with pytest.raises(HTTPException) as exc:
            _validate_password_strength("Passwortabc")
        assert exc.value.status_code == 400


class TestChangeOwnPasswordReject:
    def test_wrong_old_password_returns_403(self, write_client):
        """A non-matching old password must be rejected with 403 (not 200/500)."""
        resp = write_client.post(
            "/api/v1/auth/change-password",
            json={"old_password": "definitely-not-the-password", "new_password": "NewPass123"},
        )
        assert resp.status_code == 403
