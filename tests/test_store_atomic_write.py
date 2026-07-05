"""Atomarer JSON-Store-Write: ein Abbruch mitten im Serialisieren darf den
bestehenden Store nicht verstümmeln.

Die Stores schreiben über Temp-Datei + ``os.replace``. ``os.replace`` läuft erst
nach erfolgreichem ``json.dump`` — schlägt der Dump fehl (Crash/kill/Disk-voll,
hier simuliert durch ein nicht-serialisierbares Objekt), bleibt die bestehende
Datei unberührt. Ohne Atomarität würde ``open(path, "w")`` die Datei sofort
truncaten, der Teil-Dump bräche ab, und ``_read`` läse das beschädigte JSON als
leeren Store (``except: return []/{}``) → stiller Totalverlust.

Revert→rot: ersetzt man den atomaren Write wieder durch ``open("w")+json.dump``,
werden alle drei Tests rot (Store nach Fehlschlag leer statt unversehrt).
"""

import json

import pytest

from sp5api._paths import atomic_write_json
from sp5api.routers import absences as ab
from sp5api.routers import availability as av
from sp5api.routers import recurring_shifts as rs


class _Unserializable:
    """json.dump kann das nicht serialisieren → TypeError mitten im Schreiben."""


def test_atomic_write_json_helper_preserves_on_failure(tmp_path):
    """Der geteilte Helper (webhooks/frontend_errors/export_scheduler/scheduled_reports
    nutzen ihn) lässt die bestehende Datei bei fehlgeschlagenem Dump unversehrt — die
    alte Datei wird erst durch ``os.replace`` NACH erfolgreichem Dump ersetzt."""
    p = str(tmp_path / "store.json")
    atomic_write_json(p, [{"a": 1}], ensure_ascii=False, indent=2)
    with pytest.raises(TypeError):
        atomic_write_json(p, [{"bad": _Unserializable()}], ensure_ascii=False, indent=2)
    with open(p, encoding="utf-8") as f:
        assert json.load(f) == [{"a": 1}]  # unversehrt; nicht-atomar wäre truncated


def test_atomic_write_json_helper_no_tmp_residue_on_success(tmp_path):
    """Ein ERFOLGREICHER Write hinterlässt kein ``.tmp`` (os.replace konsumiert es)."""
    p = str(tmp_path / "store.json")
    atomic_write_json(p, [{"a": 1}], ensure_ascii=False, indent=2)
    assert not list(tmp_path.glob("*.tmp"))


_PATTERN = {
    "id": 1,
    "employee_id": 40,
    "shift_id": 1,
    "recurrence": "weekly",
    "day_of_week": 0,
    "valid_from": "2026-01-01",
    "valid_until": None,
}


def test_recurring_failed_write_preserves_store():
    rs._write_all([_PATTERN])
    assert len(rs._read_all()) == 1
    with pytest.raises(TypeError):
        rs._write_all([{"id": 2, "bad": _Unserializable()}])
    assert rs._read_all() == [_PATTERN]  # unversehrt — nicht-atomar wäre []


def test_availability_failed_write_preserves_store():
    good = {"40": {"0": [{"start": "08:00", "end": "12:00"}]}}
    av._write_all(good)
    assert av._read_all() == good
    with pytest.raises(TypeError):
        av._write_all({"41": _Unserializable()})
    assert av._read_all() == good  # unversehrt — nicht-atomar wäre {}


def test_absence_status_failed_write_preserves_store():
    good = {"1": {"status": "approved", "reject_reason": ""}}
    ab._save_absence_status(good)
    assert ab._load_absence_status() == good
    # _save_absence_status schluckt Fehler (kein raise), muss den Store aber halten
    ab._save_absence_status({"2": _Unserializable()})
    assert ab._load_absence_status() == good  # unversehrt — nicht-atomar wäre {}
