"""Tests for _int_env — the env-int parser used to wire documented config
variables (BRUTE_FORCE_*, SESSION_CLEANUP_INTERVAL_MINUTES). It must fall back
to the default on missing/invalid/negative input so a typo can't crash startup.
"""

import api.dependencies as deps


def test_returns_default_when_unset(monkeypatch):
    monkeypatch.delenv("SP5_TEST_INT", raising=False)
    assert deps._int_env("SP5_TEST_INT", 7) == 7


def test_reads_valid_int(monkeypatch):
    monkeypatch.setenv("SP5_TEST_INT", "42")
    assert deps._int_env("SP5_TEST_INT", 7) == 42


def test_invalid_value_falls_back(monkeypatch):
    monkeypatch.setenv("SP5_TEST_INT", "not-a-number")
    assert deps._int_env("SP5_TEST_INT", 7) == 7


def test_negative_value_falls_back(monkeypatch):
    monkeypatch.setenv("SP5_TEST_INT", "-3")
    assert deps._int_env("SP5_TEST_INT", 7) == 7


def test_zero_is_allowed(monkeypatch):
    monkeypatch.setenv("SP5_TEST_INT", "0")
    assert deps._int_env("SP5_TEST_INT", 7) == 0
