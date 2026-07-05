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

import pytest

from sp5api.routers import absences as ab
from sp5api.routers import availability as av
from sp5api.routers import recurring_shifts as rs


class _Unserializable:
    """json.dump kann das nicht serialisieren → TypeError mitten im Schreiben."""


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
