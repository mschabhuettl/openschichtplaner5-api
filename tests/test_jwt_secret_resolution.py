"""Tests for _resolve_jwt_secret — the JWT signing-secret resolver in
dependencies.py. A configured SP5_JWT_SECRET is used verbatim; otherwise a
random per-process secret is generated, with an operator warning in production
(but not in dev/debug) because that breaks sessions across restarts/workers.
"""

from api.dependencies import _resolve_jwt_secret


def test_configured_secret_used_verbatim_no_warning():
    secret, warning = _resolve_jwt_secret({"SP5_JWT_SECRET": "my-strong-secret"})
    assert secret == "my-strong-secret"
    assert warning is None


def test_missing_secret_in_production_warns():
    secret, warning = _resolve_jwt_secret({})  # no dev/debug → production
    assert secret and len(secret) >= 32  # strong random fallback
    assert warning is not None
    assert "SP5_JWT_SECRET" in warning


def test_missing_secret_in_dev_mode_no_warning():
    _, warning = _resolve_jwt_secret({"SP5_DEV_MODE": "true"})
    assert warning is None


def test_missing_secret_in_debug_no_warning():
    _, warning = _resolve_jwt_secret({"DEBUG": "true"})
    assert warning is None


def test_random_fallback_differs_per_call():
    s1, _ = _resolve_jwt_secret({})
    s2, _ = _resolve_jwt_secret({})
    assert s1 != s2  # token_hex → fresh each call
