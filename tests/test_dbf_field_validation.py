"""Tests for DBF field length validation — no silent truncation.

DBF field sizes (bytes) → max UTF-16-LE chars:
  5NOTE.TEXT1 / TEXT2   : C len=252 → 125 chars
  5NOTE.RESERVED        : C len=20  → 9 chars
"""

import os
import shutil

import pytest

# ─── dbf_writer._encode_string truncation warning ───────────────────────────


def test_encode_string_warns_on_truncation(caplog):
    """_encode_string() must emit a warning when value exceeds field capacity."""
    import logging

    from sp5lib.dbf_writer import _encode_string

    long_value = "A" * 200  # 200 chars × 2 bytes UTF-16 = 400 bytes > 250 max
    with caplog.at_level(logging.WARNING, logger="sp5lib.dbf_writer"):
        result = _encode_string(long_value, field_len=252)

    assert len(result) == 252, "result must always equal field_len bytes"
    assert any("truncat" in r.message.lower() for r in caplog.records), (
        "Expected a truncation warning log entry"
    )


def test_encode_string_no_warning_within_limit(caplog):
    """_encode_string() must NOT warn when value fits in the field."""
    import logging

    from sp5lib.dbf_writer import _encode_string

    short_value = "Hallo"  # 5 chars, well within 125-char limit
    with caplog.at_level(logging.WARNING, logger="sp5lib.dbf_writer"):
        result = _encode_string(short_value, field_len=252)

    assert len(result) == 252
    assert not caplog.records, "No warning expected for short value"


# ─── database.add_note / update_note raise on overflow ───────────────────────


FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture()
def tmp_db(tmp_path):
    """Copy fixture DBF files to a temp directory and return a DB instance."""
    for fname in os.listdir(FIXTURE_DIR):
        shutil.copy(os.path.join(FIXTURE_DIR, fname), tmp_path / fname)

    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from sp5lib.database import SP5Database

    db = SP5Database(str(tmp_path))
    return db


def test_add_note_too_long_text_raises(tmp_db):
    """add_note() must raise ValueError when text exceeds 125 chars."""
    long_text = "Ü" * 130  # 130 > 125
    with pytest.raises(ValueError, match="text zu lang"):
        tmp_db.add_note(date="2026-01-01", text=long_text)


def test_add_note_too_long_text2_raises(tmp_db):
    """add_note() must raise ValueError when text2 exceeds 125 chars."""
    long_text2 = "X" * 126  # 126 > 125
    with pytest.raises(ValueError, match="text2 zu lang"):
        tmp_db.add_note(date="2026-01-01", text="ok", text2=long_text2)


def test_add_note_too_long_category_raises(tmp_db):
    """add_note() must raise ValueError when category exceeds 9 chars."""
    with pytest.raises(ValueError, match="category zu lang"):
        tmp_db.add_note(date="2026-01-01", text="ok", category="Schicht-XY")  # 10 chars


def test_add_note_within_limits_succeeds(tmp_db):
    """add_note() must succeed for values within DBF field limits."""
    result = tmp_db.add_note(
        date="2026-03-06",
        text="Ü" * 125,    # exactly 125 chars → 250 bytes UTF-16-LE
        text2="ok",
        category="Schicht",  # 7 chars → ok
    )
    assert result["id"] > 0


def test_update_note_too_long_raises(tmp_db):
    """update_note() must raise ValueError for over-limit text."""
    rec = tmp_db.add_note(date="2026-01-01", text="initial")
    note_id = rec["id"]
    with pytest.raises(ValueError, match="text1 zu lang"):
        tmp_db.update_note(note_id=note_id, text1="Ö" * 130)


# ─── Pydantic model validation (NoteCreate / NoteUpdate) ─────────────────────


def test_pydantic_note_create_rejects_long_text():
    """NoteCreate must reject text longer than 125 chars with ValidationError."""
    from pydantic import ValidationError

    import sys

    # Ensure api package is importable
    api_path = os.path.join(os.path.dirname(__file__), "..")
    sys.path.insert(0, api_path)
    from api.routers.misc import NoteCreate

    with pytest.raises(ValidationError):
        NoteCreate(date="2026-01-01", text="A" * 126)


def test_pydantic_note_create_accepts_max_text():
    """NoteCreate must accept text of exactly 125 chars."""
    import sys

    api_path = os.path.join(os.path.dirname(__file__), "..")
    sys.path.insert(0, api_path)
    from api.routers.misc import NoteCreate

    model = NoteCreate(date="2026-01-01", text="Ü" * 125)
    assert len(model.text) == 125


def test_pydantic_note_create_rejects_long_category():
    """NoteCreate must reject category longer than 9 chars."""
    from pydantic import ValidationError
    import sys

    api_path = os.path.join(os.path.dirname(__file__), "..")
    sys.path.insert(0, api_path)
    from api.routers.misc import NoteCreate

    with pytest.raises(ValidationError):
        NoteCreate(date="2026-01-01", text="ok", category="1234567890")  # 10 chars
