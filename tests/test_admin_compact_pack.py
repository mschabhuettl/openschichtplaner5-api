"""Tests für POST /api/admin/compact über die lib-Fassade (Parity-Gap H-1).

PACK liegt jetzt in sp5lib (dbf_writer.pack_table / SP5Database.
compact_database): gelöschte Records werden physisch entfernt, das
-L-Journal wird geleert (Spec D-74) und stale CDX-Indizes werden
gelöscht (Spec D-14) — die Route delegiert nur noch.
"""

import os
import struct

from starlette.testclient import TestClient


def _record_count(path: str) -> int:
    with open(path, "rb") as f:
        hdr = f.read(8)
    return struct.unpack_from("<I", hdr, 4)[0]


class TestAdminCompactPack:
    def test_compact_packs_journals_and_cdx(
        self, write_client: TestClient, write_db_path
    ):
        # Soft-Delete erzeugen: Feiertag anlegen und wieder löschen
        res = write_client.post(
            "/api/holidays",
            json={"NAME": "PACK-Test", "DATE": "2031-07-01", "INTERVAL": 0},
        )
        assert res.status_code == 200, res.text
        holiday_id = res.json()["record"]["ID"]
        res = write_client.delete(f"/api/holidays/{holiday_id}")
        assert res.status_code == 200

        holid = os.path.join(write_db_path, "5HOLID.DBF")
        journal = os.path.join(write_db_path, "5HOLID-L.DBF")
        count_before = _record_count(holid)
        assert _record_count(journal) >= 2  # append + delete journalisiert

        # stale CDX simulieren — PACK muss sie entfernen (Spec D-14)
        cdx = os.path.join(write_db_path, "5HOLID.CDX")
        with open(cdx, "wb") as f:
            f.write(b"stale")

        res = write_client.post("/api/admin/compact")
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        detail = next(d for d in data["details"] if d["file"] == "5HOLID.DBF")
        assert detail["removed"] >= 1
        assert data["total_records_removed"] >= 1

        # Physisch entfernt + Header-Count reduziert
        assert _record_count(holid) == count_before - detail["removed"]
        with open(holid, "rb") as f:
            raw = f.read()
        header_size = struct.unpack_from("<H", raw, 8)[0]
        record_size = struct.unpack_from("<H", raw, 10)[0]
        assert raw[-1] == 0x1A  # EOF-Marker
        for i in range(_record_count(holid)):
            assert raw[header_size + i * record_size] != 0x2A

        # Spec D-74: Journal gezapped (Zähler-Reset), Spec D-14: CDX weg
        assert _record_count(journal) == 0
        assert not os.path.exists(cdx)

    def test_compact_requires_admin(self, planer_client: TestClient):
        res = planer_client.post("/api/admin/compact")
        assert res.status_code == 403
