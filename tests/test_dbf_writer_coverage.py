"""
Additional targeted tests to push dbf_writer.py coverage above 90%.

Covers:
  - Umlaut (ä, ö, ü, ß) round-trip via append + find_all_records
  - _encode_string: field_len=1 with non-empty value (returns b"\x20")
  - _encode_string: safety fallback path (field too small for encoded + null_term)
  - _encode_field: bytes value for C field
  - _encode_field: M type (memo) returns spaces
  - _encode_field: unknown type falls back to ascii
  - _encode_field: D field with empty string
  - _read_header_info: truncated file raises ValueError
  - append_record: empty file (file_end == 0 edge case via tiny file)
  - delete_record: negative index raises IndexError
  - update_record: negative index raises IndexError
  - _parse_record: M field and unknown field type
  - Concurrent write (basic lock sanity)
"""

import os
import struct
import sys
import tempfile
import threading
from datetime import date

_here = os.path.dirname(os.path.abspath(__file__))
_backend = os.path.dirname(_here)
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from sp5lib.dbf_writer import (  # noqa: E402
    _encode_string,
    _encode_field,
    _read_header_info,
    append_record,
    delete_record,
    update_record,
    find_all_records,
    _parse_record,
)


# ─── helpers (reused from test_write_paths) ───────────────────────────────────


def _make_field_descriptor(name, ftype, length, dec=0):
    name_bytes = name.upper().encode("ascii")[:11].ljust(11, b"\x00")
    return (
        name_bytes
        + ftype.encode("ascii")
        + b"\x00" * 4
        + bytes([length, dec])
        + b"\x00" * 14
    )


def _make_dbf(fields_spec):
    n_fields = len(fields_spec)
    record_size = 1 + sum(f[2] for f in fields_spec)
    header_size = 32 + 32 * n_fields + 1
    hdr = bytearray(32)
    hdr[0] = 0x03
    today = date.today()
    hdr[1] = today.year % 100
    hdr[2] = today.month
    hdr[3] = today.day
    struct.pack_into("<I", hdr, 4, 0)
    struct.pack_into("<H", hdr, 8, header_size)
    struct.pack_into("<H", hdr, 10, record_size)
    field_bytes = b"".join(
        _make_field_descriptor(name, ftype, length, dec)
        for name, ftype, length, dec in fields_spec
    )
    return bytes(hdr) + field_bytes + b"\x0d" + b"\x1a"


def _make_fields_list(fields_spec):
    return [
        {"name": name, "type": ftype, "len": length, "dec": dec}
        for name, ftype, length, dec in fields_spec
    ]


def _write_temp_dbf(fields_spec):
    content = _make_dbf(fields_spec)
    fd, path = tempfile.mkstemp(suffix=".DBF")
    os.close(fd)
    with open(path, "wb") as f:
        f.write(content)
    return path


SIMPLE_SPEC = [("ID", "N", 4, 0), ("NAME", "C", 40, 0)]


# ─── Umlaut round-trip ───────────────────────────────────────────────────────


def test_umlaut_roundtrip():
    """ä, ö, ü, ß survive a write→read round-trip."""
    path = _write_temp_dbf(SIMPLE_SPEC)
    fields = _make_fields_list(SIMPLE_SPEC)
    try:
        for i, name in enumerate(["Müller", "Köhler", "Weiß", "Schäfer"]):
            append_record(path, fields, {"ID": i + 1, "NAME": name})
        results = find_all_records(path, fields)
        names = {r["NAME"] for _, r in results}
        assert "Müller" in names
        assert "Köhler" in names
        assert "Weiß" in names
        assert "Schäfer" in names
    finally:
        os.unlink(path)


# ─── _encode_string edge cases ───────────────────────────────────────────────


def test_encode_string_field_len_1_nonempty():
    """field_len=1 with a non-empty value: can't fit encoded bytes, returns space."""
    result = _encode_string("A", 1)
    assert len(result) == 1
    # 'A' in UTF-16-LE is 2 bytes, field is 1 byte → encoded truncated to 0 bytes
    # then null_term=b"" (no room), padding fills 1 byte
    assert result == b"\x20"


def test_encode_string_safety_fallback():
    """When encoded+null_term+padding is shorter than field_len, padding is added."""
    # field_len=3: max_content=1 → truncate to 0 bytes (even boundary)
    # null_term: field_len - len(encoded=0) = 3 >= 2 → b"\x00\x00"
    # padding: 3 - 0 - 2 = 1 space
    result = _encode_string("Hello", 3)
    assert len(result) == 3
    assert result[0:2] == b"\x00\x00"
    assert result[2:3] == b"\x20"


# ─── _encode_field: bytes, M, unknown ────────────────────────────────────────


def test_encode_field_bytes_value():
    """Bytes passed directly to a C field are written as-is (padded)."""
    f = {"type": "C", "len": 8, "dec": 0}
    raw = b"\xde\xad\xbe\xef"
    result = _encode_field(raw, f)
    assert len(result) == 8
    assert result[:4] == b"\xde\xad\xbe\xef"
    assert result[4:] == b"\x00" * 4


def test_encode_field_memo_returns_spaces():
    """M (Memo) field should return spaces regardless of value."""
    f = {"type": "M", "len": 10, "dec": 0}
    assert _encode_field("whatever", f) == b" " * 10
    assert _encode_field(None, f) == b" " * 10


def test_encode_field_unknown_type_ascii_fallback():
    """Unknown field type falls back to ASCII ljust encoding."""
    f = {"type": "X", "len": 6, "dec": 0}
    result = _encode_field("Hi", f)
    assert len(result) == 6
    assert result == b"Hi    "


def test_encode_field_date_empty_string():
    """Empty string for a D field should return spaces."""
    f = {"type": "D", "len": 8, "dec": 0}
    result = _encode_field("", f)
    assert result == b" " * 8


# ─── _read_header_info: truncated file ───────────────────────────────────────


def test_read_header_info_truncated():
    """A file shorter than 32 bytes raises ValueError."""
    fd, path = tempfile.mkstemp(suffix=".DBF")
    os.close(fd)
    try:
        with open(path, "wb") as f:
            f.write(b"\x03" * 10)  # only 10 bytes
        try:
            _read_header_info(path)
            assert False, "Expected ValueError"
        except ValueError as e:
            assert "Truncated" in str(e)
    finally:
        os.unlink(path)


def test_read_header_info_nonexistent():
    """Non-existent file raises FileNotFoundError."""
    try:
        _read_header_info("/tmp/no_such_file_xyz_12345.DBF")
        assert False, "Expected FileNotFoundError"
    except FileNotFoundError:
        pass


# ─── delete_record: negative index ───────────────────────────────────────────


def test_delete_record_negative_index_raises():
    """Negative record index raises IndexError."""
    path = _write_temp_dbf(SIMPLE_SPEC)
    fields = _make_fields_list(SIMPLE_SPEC)
    try:
        append_record(path, fields, {"ID": 1, "NAME": "Alice"})
        try:
            delete_record(path, fields, -1)
            assert False, "Expected IndexError"
        except IndexError:
            pass
    finally:
        os.unlink(path)


# ─── update_record: negative index ───────────────────────────────────────────


def test_update_record_negative_index_raises():
    """Negative record index raises IndexError."""
    path = _write_temp_dbf(SIMPLE_SPEC)
    fields = _make_fields_list(SIMPLE_SPEC)
    try:
        append_record(path, fields, {"ID": 1, "NAME": "Alice"})
        try:
            update_record(path, fields, -1, {"NAME": "X"})
            assert False, "Expected IndexError"
        except IndexError:
            pass
    finally:
        os.unlink(path)


# ─── _parse_record: M and unknown field types ────────────────────────────────


def test_parse_record_memo_field():
    """M (Memo) field in _parse_record returns None."""
    fields = [
        {"name": "NOTE", "type": "M", "len": 10, "dec": 0},
    ]
    raw = b"\x20" + b" " * 10  # delete-flag + memo bytes
    record = _parse_record(raw, fields)
    assert record["NOTE"] is None


def test_parse_record_unknown_field_type():
    """Unknown field type is decoded as ASCII string."""
    fields = [
        {"name": "WEIRD", "type": "Z", "len": 5, "dec": 0},
    ]
    raw = b"\x20" + b"hello"
    record = _parse_record(raw, fields)
    assert record["WEIRD"] == "hello"


# ─── Concurrent write (basic lock sanity) ────────────────────────────────────


def test_concurrent_appends_no_data_loss():
    """Two threads appending concurrently should both succeed without corruption."""
    path = _write_temp_dbf(SIMPLE_SPEC)
    fields = _make_fields_list(SIMPLE_SPEC)
    errors = []

    def writer(i):
        try:
            append_record(path, fields, {"ID": i, "NAME": f"Worker{i}"})
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(1, 11)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent write errors: {errors}"
    results = find_all_records(path, fields)
    assert len(results) == 10, f"Expected 10 records, got {len(results)}"

    try:
        os.unlink(path)
    except Exception:
        pass


# ─── Graceful handling of corrupt/too-long input ─────────────────────────────


def test_too_long_string_is_truncated_not_error():
    """A string longer than the field capacity is silently truncated."""
    spec = [("ID", "N", 4, 0), ("SHORT", "C", 6, 0)]
    path = _write_temp_dbf(spec)
    fields = _make_fields_list(spec)
    try:
        # 6-byte field can hold at most 2 UTF-16 chars (4 bytes) + null-terminator
        long_name = "ABCDEFGHIJ"  # 10 chars, way too long
        count = append_record(path, fields, {"ID": 1, "SHORT": long_name})
        assert count == 1
        results = find_all_records(path, fields)
        assert len(results) == 1
        # Should not raise; value is truncated
        _, rec = results[0]
        assert isinstance(rec["SHORT"], str)
    finally:
        os.unlink(path)


if __name__ == "__main__":
    import traceback

    tests = [
        test_umlaut_roundtrip,
        test_encode_string_field_len_1_nonempty,
        test_encode_string_safety_fallback,
        test_encode_field_bytes_value,
        test_encode_field_memo_returns_spaces,
        test_encode_field_unknown_type_ascii_fallback,
        test_encode_field_date_empty_string,
        test_read_header_info_truncated,
        test_read_header_info_nonexistent,
        test_delete_record_negative_index_raises,
        test_update_record_negative_index_raises,
        test_parse_record_memo_field,
        test_parse_record_unknown_field_type,
        test_concurrent_appends_no_data_loss,
        test_too_long_string_is_truncated_not_error,
    ]

    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  OK  {t.__name__}")
            passed += 1
        except Exception:
            print(f"FAIL  {t.__name__}")
            traceback.print_exc()
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
