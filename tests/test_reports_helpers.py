"""Unit tests for the pure/format helpers in reports.py: BGR→hex colour
conversion, CSV byte decoding (UTF-8-sig with latin-1 fallback), and the
CSV-upload validation guard (content type + size)."""

import api.routers.reports as reports
import pytest
from fastapi import HTTPException


class TestIntToRgb:
    def test_black_and_white(self):
        assert reports._int_to_rgb(0) == "#000000"
        assert reports._int_to_rgb(0xFFFFFF) == "#FFFFFF"

    def test_bgr_byte_order(self):
        # stored as BGR int 0x123456 → b=0x12, g=0x34, r=0x56 → #RRGGBB
        assert reports._int_to_rgb(0x123456) == "#563412"


class TestDecodeCsv:
    def test_utf8_with_bom(self):
        data = "Näme,Wert\nÖ,1\n".encode("utf-8-sig")
        assert reports._decode_csv(data).startswith("Näme")

    def test_latin1_fallback(self):
        # latin-1 bytes for ö/ü are invalid UTF-8 → falls back to latin-1
        data = "Jörg;Müller".encode("latin-1")
        assert reports._decode_csv(data) == "Jörg;Müller"


class _FakeUpload:
    def __init__(self, content_type, data):
        self.content_type = content_type
        self._data = data

    async def read(self):
        return self._data


class TestValidateCsvUpload:
    async def test_rejects_non_csv_content_type(self):
        with pytest.raises(HTTPException) as exc:
            await reports._validate_csv_upload(_FakeUpload("image/png", b"x"))
        assert exc.value.status_code == 400

    async def test_rejects_oversized_file(self):
        big = b"x" * (reports._MAX_CSV_SIZE + 1)
        with pytest.raises(HTTPException) as exc:
            await reports._validate_csv_upload(_FakeUpload("text/csv", big))
        assert exc.value.status_code == 413

    async def test_accepts_valid_csv(self):
        content = await reports._validate_csv_upload(
            _FakeUpload("text/csv; charset=utf-8", b"NAME,FIRSTNAME\nMueller,Anna\n")
        )
        assert content.startswith(b"NAME,FIRSTNAME")
