"""Tests for _open_log_handler — honours the documented LOG_FILE env var by
creating the parent directory, and falls back to the default /tmp path on any
filesystem error so a misconfigured LOG_FILE can never break startup.
"""

import os

import api.dependencies as deps


def test_creates_parent_dir_and_uses_path(tmp_path):
    target = str(tmp_path / "logs" / "sp5.log")
    used, handler = deps._open_log_handler(target)
    try:
        assert used == target
        assert os.path.isdir(os.path.dirname(target))
    finally:
        handler.close()


def test_falls_back_when_path_unusable(tmp_path):
    # Make the parent a FILE so makedirs under it raises → fallback to default.
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    bad = str(blocker / "sub" / "sp5.log")
    used, handler = deps._open_log_handler(bad)
    try:
        assert used == deps._DEFAULT_LOG_FILE
    finally:
        handler.close()
