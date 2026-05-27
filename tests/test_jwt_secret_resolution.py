"""Tests for _resolve_jwt_secret — the JWT signing-secret resolver in
dependencies.py.

It must honour SECRET_KEY (the documented var that `start.sh` auto-generates)
as well as the SP5_JWT_SECRET alias, treat the shipped ``change-me…``
placeholder as unset, and otherwise fall back to a random per-process secret
with an operator warning in production (but not in dev/debug).
"""

from api.dependencies import _resolve_jwt_secret

_PLACEHOLDER = "change-me-in-production-use-openssl-rand-hex-32"


def test_sp5_jwt_secret_used_verbatim_no_warning():
    secret, warning = _resolve_jwt_secret({"SP5_JWT_SECRET": "my-strong-secret"})
    assert secret == "my-strong-secret"
    assert warning is None


def test_secret_key_is_honoured():
    """SECRET_KEY is the documented + auto-generated var — it MUST sign tokens."""
    secret, warning = _resolve_jwt_secret({"SECRET_KEY": "a-real-generated-key"})
    assert secret == "a-real-generated-key"
    assert warning is None


def test_sp5_jwt_secret_takes_precedence_over_secret_key():
    secret, _ = _resolve_jwt_secret({"SP5_JWT_SECRET": "primary", "SECRET_KEY": "secondary"})
    assert secret == "primary"


def test_placeholder_secret_key_treated_as_unset_and_warns():
    secret, warning = _resolve_jwt_secret({"SECRET_KEY": _PLACEHOLDER})
    assert secret != _PLACEHOLDER  # placeholder must not sign tokens
    assert len(secret) >= 32
    assert warning is not None


def test_missing_secret_in_production_warns():
    secret, warning = _resolve_jwt_secret({})  # no dev/debug → production
    assert secret and len(secret) >= 32
    assert warning is not None
    assert "SECRET_KEY" in warning


def test_missing_secret_in_dev_mode_no_warning():
    _, warning = _resolve_jwt_secret({"SP5_DEV_MODE": "true"})
    assert warning is None


def test_placeholder_in_debug_no_warning():
    _, warning = _resolve_jwt_secret({"SECRET_KEY": _PLACEHOLDER, "DEBUG": "true"})
    assert warning is None


def test_random_fallback_differs_per_call():
    s1, _ = _resolve_jwt_secret({})
    s2, _ = _resolve_jwt_secret({})
    assert s1 != s2
