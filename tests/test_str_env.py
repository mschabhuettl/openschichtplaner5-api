"""Tests for _str_env — the string-env reader used to make RATE_LIMIT_API /
RATE_LIMIT_LOGIN configurable. Empty/whitespace/missing → default.
"""

import api.dependencies as deps


def test_returns_default_when_unset(monkeypatch):
    monkeypatch.delenv("SP5_TEST_STR", raising=False)
    assert deps._str_env("SP5_TEST_STR", "5/minute") == "5/minute"


def test_reads_value(monkeypatch):
    monkeypatch.setenv("SP5_TEST_STR", "20/minute")
    assert deps._str_env("SP5_TEST_STR", "5/minute") == "20/minute"


def test_blank_falls_back(monkeypatch):
    monkeypatch.setenv("SP5_TEST_STR", "   ")
    assert deps._str_env("SP5_TEST_STR", "5/minute") == "5/minute"


def test_login_rate_limit_default_is_documented_value():
    """Unset in the test env → matches the documented default (5/minute)."""
    assert deps._LOGIN_RATE_LIMIT == "5/minute"
