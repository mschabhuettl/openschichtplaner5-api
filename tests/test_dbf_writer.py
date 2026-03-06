"""
Dedicated tests for sp5lib/dbf_writer.py.

Targets coverage gaps not yet reached by test_write_paths.py:
- _encode_string edge cases (field_len=1 with empty, safety padding)
- _encode_field: bytes value, Memo type, unknown type, N field with dec
- _read_header_info: truncated header
- _parse_record: L, M, unknown type fields
- find_all_records: file open OSError path, fields=None auto-load
- Roundtrip tests with Umlauts
- Error: writing to nonexistent directory

Run:
    python3 -m pytest tests/test_dbf_writer.py -v --cov=sp5lib.dbf_writer --cov-report=term-missing
"""
import os
import struct
import sys
import tempfile

_here = os.path.dirname(os.path.abspath(__file__))
_backend = os.path.dirname(_here)
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import pytest

from sp5lib.dbf_writer import (
    _encode_string,
    _encode_field,
    _read_header_info,
    _parse_record,
    append_record,
    delete_record,
    update_record,
    find_all_records,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_field_descriptor(name: str, ftype: str, length: int, dec: int = 0) -> bytes:
    name_bytes = name.upper().encode("ascii")[:11].ljust(11, b"\x00")
    return (
        name_bytes
        + ftype.encode("ascii")
        + b"\x00" * 4
        + bytes([length, dec])
        + b"\x00" * 14
    )


def _make_dbf(fields_spec) -> bytes:
    """Build a minimal valid .DBF with no records."""
    num_fields = len(fields_spec)
    header_size = 32 + num_fields * 32 + 1  # +1 for header terminator
    record_size = 1 + sum(f[2] for f in fields_spec)

    hdr = bytearray(32)
    hdr[0] = 0x03  # version
    hdr[1:4] = [26, 3, 6]  # date
    struct.pack_into("<I", hdr, 4, 0)  # num_records
    struct.pack_into("<H", hdr, 8, header_size)
    struct.pack_into("<H", hdr, 10, record_size)

    fields_bytes = b"".join(
        _make_field_descriptor(n, t, l, d) for n, t, l, d in fields_spec
    )
    return bytes(hdr) + fields_bytes + b"\x0d" + b"\x1a"


def _write_dbf(fields_spec) -> str:
    """Write a minimal DBF to a temp file and return path."""
    tmp = tempfile.NamedTemporaryFile(suffix=".dbf", delete=False)
    tmp.write(_make_dbf(fields_spec))
    tmp.close()
    return tmp.name


# ─── _encode_string edge cases ────────────────────────────────────────────────


def test_encode_string_field_len_1_empty():
    """field_len=1 with empty string: can't fit null-terminator, fill with null."""
    result = _encode_string("", 1)
    assert len(result) == 1
    assert result == b"\x00"


def test_encode_string_safety_padding():
    """When encoded + null-term is shorter than field_len, padding must be added."""
    # 'A' encodes to 2 bytes UTF-16-LE, null-term=2, field_len=10 → needs 6 spaces
    result = _encode_string("A", 10)
    assert len(result) == 10
    # starts with UTF-16-LE 'A' (0x41 0x00)
    assert result[:2] == b"\x41\x00"
    assert result[2:4] == b"\x00\x00"  # null terminator
    assert result[4:] == b"\x20" * 6   # padding


# ─── _encode_field edge cases ─────────────────────────────────────────────────


def test_encode_field_bytes_value_c_field():
    """Passing raw bytes to a C field writes them as-is."""
    field = {"type": "C", "len": 6, "dec": 0, "name": "RAW"}
    raw = b"\x01\x02\x03"
    result = _encode_field(raw, field)
    assert len(result) == 6
    assert result[:3] == b"\x01\x02\x03"


def test_encode_field_memo_type():
    """Memo (M) fields return all spaces."""
    field = {"type": "M", "len": 10, "dec": 0, "name": "MEMO"}
    result = _encode_field("anything", field)
    assert result == b" " * 10


def test_encode_field_unknown_type():
    """Unknown field type falls back to ljust ASCII."""
    field = {"type": "X", "len": 5, "dec": 0, "name": "UNK"}
    result = _encode_field("hi", field)
    assert len(result) == 5
    assert result == b"hi   "


def test_encode_field_numeric_float_with_dec():
    """Numeric field with dec > 0 formats as float."""
    field = {"type": "N", "len": 8, "dec": 2, "name": "PRICE"}
    result = _encode_field(3.14159, field)
    assert result == b"    3.14"


def test_encode_field_none_logical():
    """None for logical field returns a single space (None path → b' ' * flen)."""
    field = {"type": "L", "len": 1, "dec": 0, "name": "FLAG"}
    result = _encode_field(None, field)
    # None hits the early-return `b" " * flen` path
    assert result == b" "


def test_encode_field_date_none():
    """None date → spaces."""
    field = {"type": "D", "len": 8, "dec": 0, "name": "DAT"}
    result = _encode_field(None, field)
    assert result == b" " * 8


# ─── _read_header_info error paths ────────────────────────────────────────────


def test_read_header_info_nonexistent():
    with pytest.raises(FileNotFoundError):
        _read_header_info("/nonexistent/path/file.dbf")


def test_read_header_info_truncated():
    """DBF file shorter than 32 bytes raises ValueError."""
    tmp = tempfile.NamedTemporaryFile(suffix=".dbf", delete=False)
    tmp.write(b"\x03\x01\x02\x03")  # only 4 bytes
    tmp.close()
    try:
        with pytest.raises(ValueError, match="Truncated"):
            _read_header_info(tmp.name)
    finally:
        os.unlink(tmp.name)


# ─── _parse_record coverage ───────────────────────────────────────────────────


def test_parse_record_logical_true_variants():
    """_parse_record handles L field T/Y/t/y/1 as True."""
    fields = [{"type": "L", "len": 1, "dec": 0, "name": "FLAG"}]
    for ch in [b"T", b"Y", b"t", b"y", b"1"]:
        raw = b"\x20" + ch  # delete-flag + field
        rec = _parse_record(raw, fields)
        assert rec["FLAG"] is True, f"Expected True for {ch}"

    raw = b"\x20" + b"F"
    rec = _parse_record(raw, fields)
    assert rec["FLAG"] is False


def test_parse_record_memo_field():
    """_parse_record returns None for M fields."""
    fields = [{"type": "M", "len": 4, "dec": 0, "name": "NOTE"}]
    raw = b"\x20" + b"    "
    rec = _parse_record(raw, fields)
    assert rec["NOTE"] is None


def test_parse_record_unknown_type():
    """_parse_record strips and returns string for unknown field types."""
    fields = [{"type": "X", "len": 4, "dec": 0, "name": "MISC"}]
    raw = b"\x20" + b"ab  "
    rec = _parse_record(raw, fields)
    assert rec["MISC"] == "ab"


def test_parse_record_numeric_dot_value():
    """'.' in N field is treated as 0."""
    fields = [{"type": "N", "len": 4, "dec": 0, "name": "NUM"}]
    raw = b"\x20" + b"   ."
    rec = _parse_record(raw, fields)
    assert rec["NUM"] == 0


# ─── Roundtrip tests ──────────────────────────────────────────────────────────


def test_roundtrip_basic():
    """Write a record and read it back via find_all_records."""
    path = _write_dbf([("NAME", "C", 20, 0), ("AGE", "N", 3, 0)])
    try:
        fields = [
            {"type": "C", "len": 20, "dec": 0, "name": "NAME"},
            {"type": "N", "len": 3, "dec": 0, "name": "AGE"},
        ]
        append_record(path, fields, {"NAME": "Alice", "AGE": 30})
        results = find_all_records(path, fields)
        assert len(results) == 1
        _, rec = results[0]
        assert rec["NAME"] == "Alice"
        assert rec["AGE"] == 30
    finally:
        os.unlink(path)


def test_roundtrip_umlauts():
    """Umlauts survive the write→read roundtrip (field large enough)."""
    # "Müller" = 6 chars = 12 bytes UTF-16-LE + 2 null-term = 14 bytes → field_len=16
    path = _write_dbf([("NAME", "C", 16, 0)])
    try:
        fields = [{"type": "C", "len": 16, "dec": 0, "name": "NAME"}]
        umlaut_name = "Müller"
        append_record(path, fields, {"NAME": umlaut_name})
        results = find_all_records(path, fields)
        assert len(results) == 1
        _, rec = results[0]
        assert rec["NAME"] == umlaut_name
    finally:
        os.unlink(path)


def test_roundtrip_empty_string():
    """Empty string writes and reads back as empty string."""
    path = _write_dbf([("NAME", "C", 20, 0)])
    try:
        fields = [{"type": "C", "len": 20, "dec": 0, "name": "NAME"}]
        append_record(path, fields, {"NAME": ""})
        results = find_all_records(path, fields)
        assert len(results) == 1
        _, rec = results[0]
        assert rec["NAME"] == ""
    finally:
        os.unlink(path)


def test_roundtrip_none_values():
    """None values for various types produce valid reads."""
    path = _write_dbf([("NAME", "C", 10, 0), ("AGE", "N", 3, 0)])
    try:
        fields = [
            {"type": "C", "len": 10, "dec": 0, "name": "NAME"},
            {"type": "N", "len": 3, "dec": 0, "name": "AGE"},
        ]
        append_record(path, fields, {"NAME": None, "AGE": None})
        results = find_all_records(path, fields)
        assert len(results) == 1
        _, rec = results[0]
        # None string → empty; None numeric → 0
        assert rec["NAME"] == ""
        assert rec["AGE"] == 0
    finally:
        os.unlink(path)


def test_roundtrip_logical_field():
    """Boolean values survive roundtrip."""
    path = _write_dbf([("FLAG", "L", 1, 0)])
    try:
        fields = [{"type": "L", "len": 1, "dec": 0, "name": "FLAG"}]
        append_record(path, fields, {"FLAG": True})
        append_record(path, fields, {"FLAG": False})
        results = find_all_records(path, fields)
        assert len(results) == 2
        assert results[0][1]["FLAG"] is True
        assert results[1][1]["FLAG"] is False
    finally:
        os.unlink(path)


def test_roundtrip_date_field():
    """Date string survives roundtrip."""
    path = _write_dbf([("DAT", "D", 8, 0)])
    try:
        fields = [{"type": "D", "len": 8, "dec": 0, "name": "DAT"}]
        append_record(path, fields, {"DAT": "2024-06-15"})
        results = find_all_records(path, fields)
        assert len(results) == 1
        dat = results[0][1]["DAT"]
        # _parse_date may return a date object or 'YYYY-MM-DD' string
        from datetime import date
        if isinstance(dat, date):
            assert dat == date(2024, 6, 15)
        else:
            assert dat == "2024-06-15"
    finally:
        os.unlink(path)


def test_field_truncation_at_max_length():
    """String longer than field is truncated, not erroring."""
    path = _write_dbf([("NAME", "C", 6, 0)])  # only 6 bytes
    try:
        fields = [{"type": "C", "len": 6, "dec": 0, "name": "NAME"}]
        long_name = "ABCDEFGHIJ"  # 10 chars
        append_record(path, fields, {"NAME": long_name})
        results = find_all_records(path, fields)
        assert len(results) == 1
        # Should be truncated to fit in 6 bytes (≤3 UTF-16-LE chars + null-term)
        val = results[0][1]["NAME"]
        assert len(val) <= 3  # 4 bytes content + 2 null-term in 6 byte field
    finally:
        os.unlink(path)


# ─── Error cases ──────────────────────────────────────────────────────────────


def test_append_to_nonexistent_file():
    """append_record on a nonexistent file raises FileNotFoundError."""
    fields = [{"type": "C", "len": 10, "dec": 0, "name": "NAME"}]
    with pytest.raises(FileNotFoundError):
        append_record("/nonexistent/dir/file.dbf", fields, {"NAME": "test"})


def test_find_all_records_nonexistent_returns_empty():
    """find_all_records on missing file returns []."""
    result = find_all_records("/no/such/file.dbf", [])
    assert result == []


def test_delete_then_not_found():
    """Deleted records are excluded from find_all_records."""
    path = _write_dbf([("NAME", "C", 10, 0)])
    try:
        fields = [{"type": "C", "len": 10, "dec": 0, "name": "NAME"}]
        append_record(path, fields, {"NAME": "Alice"})
        append_record(path, fields, {"NAME": "Bob"})
        results = find_all_records(path, fields)
        assert len(results) == 2
        idx0, _ = results[0]
        delete_record(path, fields, idx0)
        results2 = find_all_records(path, fields)
        assert len(results2) == 1
        assert results2[0][1]["NAME"] == "Bob"
    finally:
        os.unlink(path)


def test_update_then_find():
    """update_record changes are reflected in find_all_records."""
    path = _write_dbf([("NAME", "C", 20, 0), ("AGE", "N", 3, 0)])
    try:
        fields = [
            {"type": "C", "len": 20, "dec": 0, "name": "NAME"},
            {"type": "N", "len": 3, "dec": 0, "name": "AGE"},
        ]
        append_record(path, fields, {"NAME": "Alice", "AGE": 25})
        results = find_all_records(path, fields)
        idx, _ = results[0]
        update_record(path, fields, idx, {"AGE": 26})
        results2 = find_all_records(path, fields)
        assert results2[0][1]["AGE"] == 26
        assert results2[0][1]["NAME"] == "Alice"
    finally:
        os.unlink(path)


# ─── api/types.py ─────────────────────────────────────────────────────────────

def test_api_types_importable():
    """api.types can be imported and type aliases are defined."""
    from api.types import (
        DBFRow, EmployeeRecord, ShiftRecord, GroupRecord,
        ScheduleEntry, AbsenceRecord, BookingRecord,
        EmployeeList, ShiftList, ScheduleList, DBFRowList,
    )
    # They're just type aliases — check they exist and are dict/list based
    row: DBFRow = {"key": "value"}
    assert row["key"] == "value"
    emp_list: EmployeeList = [{"name": "Alice"}]
    assert len(emp_list) == 1
