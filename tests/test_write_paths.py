"""
Write-path tests for dbf_writer.py and related database logic.

Run with:
    python -m pytest backend/tests/test_write_paths.py  (or just python test_write_paths.py)
No external dependencies required — only stdlib + the sp5lib module.
"""

import os
import struct
import sys
import tempfile
from datetime import date

# Make sp5lib importable when run from the backend/ or repo root
_here = os.path.dirname(os.path.abspath(__file__))
_backend = os.path.dirname(_here)
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from sp5lib.dbf_writer import (  # noqa: E402
    _encode_string,
    _encode_field,
    append_record,
    delete_record,
    update_record,
    find_all_records,
)


# ─── Minimal DBF builder ──────────────────────────────────────────────────────

def _make_field_descriptor(name: str, ftype: str, length: int, dec: int = 0) -> bytes:
    """Return a 32-byte DBF field descriptor."""
    name_bytes = name.upper().encode('ascii')[:11].ljust(11, b'\x00')
    return (
        name_bytes
        + ftype.encode('ascii')
        + b'\x00' * 4          # reserved
        + bytes([length, dec])
        + b'\x00' * 14         # reserved
    )


def _make_dbf(fields_spec) -> bytes:
    """
    Build a minimal, valid .DBF byte-string with no records.

    fields_spec: list of (name, type, length, dec) tuples.
    """
    n_fields = len(fields_spec)
    record_size = 1 + sum(f[2] for f in fields_spec)
    header_size = 32 + 32 * n_fields + 1  # 32 hdr + 32*N fields + terminator

    hdr = bytearray(32)
    hdr[0] = 0x03  # dBASE III
    today = date.today()
    hdr[1] = today.year % 100
    hdr[2] = today.month
    hdr[3] = today.day
    struct.pack_into('<I', hdr, 4, 0)            # num_records = 0
    struct.pack_into('<H', hdr, 8, header_size)
    struct.pack_into('<H', hdr, 10, record_size)

    field_bytes = b''.join(
        _make_field_descriptor(name, ftype, length, dec)
        for name, ftype, length, dec in fields_spec
    )

    return bytes(hdr) + field_bytes + b'\x0d' + b'\x1a'


def _make_fields_list(fields_spec):
    """Return a list-of-dicts matching the format returned by get_table_fields()."""
    return [
        {'name': name, 'type': ftype, 'len': length, 'dec': dec}
        for name, ftype, length, dec in fields_spec
    ]


def _write_temp_dbf(fields_spec) -> str:
    """Write a minimal DBF to a temp file and return its path."""
    content = _make_dbf(fields_spec)
    fd, path = tempfile.mkstemp(suffix='.DBF')
    os.close(fd)
    with open(path, 'wb') as f:
        f.write(content)
    return path


# ─── _encode_string ───────────────────────────────────────────────────────────

def test_encode_string_empty():
    result = _encode_string('', 10)
    assert len(result) == 10
    assert result[:2] == b'\x00\x00'           # null terminator for empty string
    assert result[2:] == b'\x20' * 8           # space padding

def test_encode_string_simple():
    result = _encode_string('A', 10)
    assert len(result) == 10
    # 'A' in UTF-16-LE is b'\x41\x00'
    assert result[:2] == b'\x41\x00'
    assert result[2:4] == b'\x00\x00'          # null terminator
    assert result[4:] == b'\x20' * 6           # padding

def test_encode_string_too_long_truncates():
    # 6-byte string field can only hold 2 UTF-16 chars (4 bytes) + null terminator
    result = _encode_string('ABCDEF', 6)
    assert len(result) == 6
    # Should have truncated to 2 chars = 4 bytes + 2 null bytes
    assert result[4:6] == b'\x00\x00'

def test_encode_string_zero_length():
    assert _encode_string('hello', 0) == b''

def test_encode_string_tiny_field():
    # field_len=1: can't fit null terminator, gets a space
    result = _encode_string('X', 1)
    assert len(result) == 1
    assert result == b'\x20'

def test_encode_string_field_len_2():
    # field_len=2: exactly a null terminator
    result = _encode_string('', 2)
    assert result == b'\x00\x00'


# ─── _encode_field ────────────────────────────────────────────────────────────

def test_encode_field_none_returns_spaces():
    f = {'type': 'C', 'len': 8, 'dec': 0}
    result = _encode_field(None, f)
    assert result == b' ' * 8

def test_encode_field_numeric_int():
    f = {'type': 'N', 'len': 5, 'dec': 0}
    result = _encode_field(42, f)
    assert result == b'   42'

def test_encode_field_numeric_float():
    f = {'type': 'N', 'len': 8, 'dec': 2}
    result = _encode_field(3.14, f)
    assert result == b'    3.14'

def test_encode_field_numeric_invalid_becomes_spaces():
    f = {'type': 'N', 'len': 5, 'dec': 0}
    result = _encode_field('not_a_number', f)
    assert result == b' ' * 5

def test_encode_field_logical_true():
    f = {'type': 'L', 'len': 1, 'dec': 0}
    assert _encode_field(True, f) == b'T'
    assert _encode_field(1, f) == b'T'

def test_encode_field_logical_false():
    f = {'type': 'L', 'len': 1, 'dec': 0}
    assert _encode_field(False, f) == b'F'
    assert _encode_field(0, f) == b'F'

def test_encode_field_date_iso():
    f = {'type': 'D', 'len': 8, 'dec': 0}
    result = _encode_field('2024-03-15', f)
    assert result == b'20240315'

def test_encode_field_date_compact():
    f = {'type': 'D', 'len': 8, 'dec': 0}
    result = _encode_field('20240315', f)
    assert result == b'20240315'

def test_encode_field_date_invalid_becomes_spaces():
    f = {'type': 'D', 'len': 8, 'dec': 0}
    result = _encode_field('not-a-date', f)
    assert result == b' ' * 8


# ─── append_record ────────────────────────────────────────────────────────────

SIMPLE_FIELDS_SPEC = [
    ('ID',   'N',  4, 0),
    ('NAME', 'C', 20, 0),
]

def test_append_record_basic():
    path = _write_temp_dbf(SIMPLE_FIELDS_SPEC)
    fields = _make_fields_list(SIMPLE_FIELDS_SPEC)
    try:
        count = append_record(path, fields, {'ID': 1, 'NAME': 'Alice'})
        assert count == 1, f"Expected 1, got {count}"

        # Verify we can read it back
        results = find_all_records(path, fields)
        assert len(results) == 1
        idx, rec = results[0]
        assert rec['ID'] == 1
        assert rec['NAME'] == 'Alice'
    finally:
        os.unlink(path)

def test_append_record_multiple():
    path = _write_temp_dbf(SIMPLE_FIELDS_SPEC)
    fields = _make_fields_list(SIMPLE_FIELDS_SPEC)
    try:
        append_record(path, fields, {'ID': 1, 'NAME': 'Alice'})
        append_record(path, fields, {'ID': 2, 'NAME': 'Bob'})
        count = append_record(path, fields, {'ID': 3, 'NAME': 'Charlie'})
        assert count == 3

        results = find_all_records(path, fields)
        assert len(results) == 3
        names = [r['NAME'] for _, r in results]
        assert 'Alice' in names
        assert 'Bob' in names
        assert 'Charlie' in names
    finally:
        os.unlink(path)

def test_append_record_missing_fields_default_to_none():
    path = _write_temp_dbf(SIMPLE_FIELDS_SPEC)
    fields = _make_fields_list(SIMPLE_FIELDS_SPEC)
    try:
        # NAME intentionally omitted → _encode_field(None, C field) → b' '*20
        # _decode_string of all-spaces returns some string (not an exception)
        append_record(path, fields, {'ID': 99})
        results = find_all_records(path, fields)
        assert len(results) == 1
        _, rec = results[0]
        assert rec['ID'] == 99
        assert rec['NAME'] is not None  # some string value, not an exception
    finally:
        os.unlink(path)


# ─── delete_record ────────────────────────────────────────────────────────────

def test_delete_record_marks_deleted():
    path = _write_temp_dbf(SIMPLE_FIELDS_SPEC)
    fields = _make_fields_list(SIMPLE_FIELDS_SPEC)
    try:
        append_record(path, fields, {'ID': 1, 'NAME': 'Alice'})
        delete_record(path, fields, 0)  # raw_index = 0

        # find_all_records should skip deleted records
        results = find_all_records(path, fields)
        assert results == [], f"Expected no results, got {results}"

        # Header count still says 1 (soft-delete does not decrement count)
        with open(path, 'rb') as f:
            hdr = f.read(12)
        count = struct.unpack_from('<I', hdr, 4)[0]
        assert count == 1, f"Expected header count=1, got {count}"

        # Verify the delete flag byte (0x2A) is set at record offset
        header_size_bytes = struct.unpack_from('<H', hdr, 8)[0]
        with open(path, 'rb') as f:
            f.seek(header_size_bytes)
            flag = f.read(1)
        assert flag == b'\x2a', f"Expected delete flag 0x2A, got {flag!r}"
    finally:
        os.unlink(path)

def test_delete_record_out_of_range_raises():
    path = _write_temp_dbf(SIMPLE_FIELDS_SPEC)
    fields = _make_fields_list(SIMPLE_FIELDS_SPEC)
    try:
        append_record(path, fields, {'ID': 1, 'NAME': 'Alice'})
        try:
            delete_record(path, fields, 5)  # out of range
            assert False, "Expected IndexError"
        except IndexError:
            pass
    finally:
        os.unlink(path)

def test_delete_already_deleted_is_idempotent():
    """Deleting an already-deleted record should be a no-op (not raise)."""
    path = _write_temp_dbf(SIMPLE_FIELDS_SPEC)
    fields = _make_fields_list(SIMPLE_FIELDS_SPEC)
    try:
        append_record(path, fields, {'ID': 1, 'NAME': 'X'})
        delete_record(path, fields, 0)
        delete_record(path, fields, 0)  # second delete: should not raise
    finally:
        os.unlink(path)


# ─── update_record ────────────────────────────────────────────────────────────

def test_update_record_changes_field():
    path = _write_temp_dbf(SIMPLE_FIELDS_SPEC)
    fields = _make_fields_list(SIMPLE_FIELDS_SPEC)
    try:
        append_record(path, fields, {'ID': 1, 'NAME': 'Alice'})
        update_record(path, fields, 0, {'NAME': 'Bob'})

        results = find_all_records(path, fields)
        assert len(results) == 1
        _, rec = results[0]
        assert rec['NAME'] == 'Bob'
        assert rec['ID'] == 1  # unchanged
    finally:
        os.unlink(path)

def test_update_record_does_not_touch_other_records():
    path = _write_temp_dbf(SIMPLE_FIELDS_SPEC)
    fields = _make_fields_list(SIMPLE_FIELDS_SPEC)
    try:
        append_record(path, fields, {'ID': 1, 'NAME': 'Alice'})
        append_record(path, fields, {'ID': 2, 'NAME': 'Bob'})
        update_record(path, fields, 0, {'NAME': 'Updated'})

        results = find_all_records(path, fields)
        assert len(results) == 2
        by_id = {r['ID']: r for _, r in results}
        assert by_id[1]['NAME'] == 'Updated'
        assert by_id[2]['NAME'] == 'Bob'  # untouched
    finally:
        os.unlink(path)

def test_update_deleted_record_raises():
    path = _write_temp_dbf(SIMPLE_FIELDS_SPEC)
    fields = _make_fields_list(SIMPLE_FIELDS_SPEC)
    try:
        append_record(path, fields, {'ID': 1, 'NAME': 'Alice'})
        delete_record(path, fields, 0)
        try:
            update_record(path, fields, 0, {'NAME': 'Should fail'})
            assert False, "Expected ValueError for updating a deleted record"
        except ValueError:
            pass
    finally:
        os.unlink(path)

def test_update_record_out_of_range_raises():
    path = _write_temp_dbf(SIMPLE_FIELDS_SPEC)
    fields = _make_fields_list(SIMPLE_FIELDS_SPEC)
    try:
        append_record(path, fields, {'ID': 1, 'NAME': 'Alice'})
        try:
            update_record(path, fields, 99, {'NAME': 'X'})
            assert False, "Expected IndexError"
        except IndexError:
            pass
    finally:
        os.unlink(path)


# ─── find_all_records ─────────────────────────────────────────────────────────

def test_find_all_records_filter():
    path = _write_temp_dbf(SIMPLE_FIELDS_SPEC)
    fields = _make_fields_list(SIMPLE_FIELDS_SPEC)
    try:
        append_record(path, fields, {'ID': 1, 'NAME': 'Alice'})
        append_record(path, fields, {'ID': 2, 'NAME': 'Bob'})
        append_record(path, fields, {'ID': 3, 'NAME': 'Alice'})

        matches = find_all_records(path, fields, NAME='Alice')
        assert len(matches) == 2
        ids = [r['ID'] for _, r in matches]
        assert 1 in ids
        assert 3 in ids
    finally:
        os.unlink(path)

def test_find_all_records_nonexistent_file():
    result = find_all_records('/tmp/nonexistent_12345.DBF', [])
    assert result == []


# ─── Validation logic (no HTTP calls) ────────────────────────────────────────

def test_validdays_validation():
    """Reproduce the VALIDDAYS validation logic from the API endpoint."""
    def is_valid(v):
        return len(v) == 7 and all(c in '01' for c in v)

    assert is_valid('1111100')
    assert is_valid('0000000')
    assert is_valid('1010101')
    assert not is_valid('111110')   # too short
    assert not is_valid('11111000') # too long
    assert not is_valid('1111120')  # invalid char
    assert not is_valid('')

def test_period_date_ordering():
    """start must be <= end."""
    def is_valid(start, end):
        return end >= start

    assert is_valid('2024-01-01', '2024-01-31')
    assert is_valid('2024-01-01', '2024-01-01')  # same day OK
    assert not is_valid('2024-02-01', '2024-01-01')

def test_staffing_requirement_validation():
    """weekday 0-6, min >= 0, max >= min."""
    def validate(weekday, min_v, max_v):
        if not (0 <= weekday <= 6):
            return 'weekday out of range'
        if min_v < 0:
            return 'min negative'
        if max_v < min_v:
            return 'max < min'
        return 'ok'

    assert validate(0, 0, 5) == 'ok'
    assert validate(6, 2, 2) == 'ok'
    assert validate(7, 0, 0) == 'weekday out of range'
    assert validate(-1, 0, 0) == 'weekday out of range'
    assert validate(1, -1, 5) == 'min negative'
    assert validate(1, 5, 3) == 'max < min'

def test_name_validation():
    """Empty or whitespace-only NAME must be rejected."""
    def is_valid_name(name):
        return bool(name and name.strip())

    assert is_valid_name('Alice')
    assert is_valid_name(' Alice ')  # strip() handles surrounding whitespace
    assert not is_valid_name('')
    assert not is_valid_name('   ')
    assert not is_valid_name(None)


# ─── run all tests ────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import traceback

    tests = [
        test_encode_string_empty,
        test_encode_string_simple,
        test_encode_string_too_long_truncates,
        test_encode_string_zero_length,
        test_encode_string_tiny_field,
        test_encode_string_field_len_2,
        test_encode_field_none_returns_spaces,
        test_encode_field_numeric_int,
        test_encode_field_numeric_float,
        test_encode_field_numeric_invalid_becomes_spaces,
        test_encode_field_logical_true,
        test_encode_field_logical_false,
        test_encode_field_date_iso,
        test_encode_field_date_compact,
        test_encode_field_date_invalid_becomes_spaces,
        test_append_record_basic,
        test_append_record_multiple,
        test_append_record_missing_fields_default_to_none,
        test_delete_record_marks_deleted,
        test_delete_record_out_of_range_raises,
        test_delete_already_deleted_is_idempotent,
        test_update_record_changes_field,
        test_update_record_does_not_touch_other_records,
        test_update_deleted_record_raises,
        test_update_record_out_of_range_raises,
        test_find_all_records_filter,
        test_find_all_records_nonexistent_file,
        test_validdays_validation,
        test_period_date_ordering,
        test_staffing_requirement_validation,
        test_name_validation,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f'  OK  {t.__name__}')
            passed += 1
        except Exception:
            print(f'FAIL  {t.__name__}')
            traceback.print_exc()
            failed += 1

    print(f'\n{passed} passed, {failed} failed')
    sys.exit(0 if failed == 0 else 1)
