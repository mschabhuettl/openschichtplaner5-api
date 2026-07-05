"""Regressionsschutz: die JSON-Document-Stores sind pro Test isoliert.

Ohne die autouse-Fixture ``_state_stores_in_tmp`` (conftest) teilen sich alle Tests je
eine einzige Store-Datei — jeder ``_*_FILE`` ist eine import-fixe Modul-Konstante auf
einen geteilten Pfad. Ein Eintrag, den ein Test hinterlässt, war dann für spätere Tests
sichtbar: Ursache seltener, reihenfolgeabhängiger Voll-Lauf-Flakes (etwa
``test_recurring_shifts::test_delete_existing`` mit Endzusicherung ``GET == []``).

Je Store laufen zwei Tests in Definitionsreihenfolge: der erste schreibt einen Eintrag
und räumt NICHT auf, der zweite verlangt einen leeren Store. Mit Isolation sind beide
grün; entfernt man die Fixture, sieht der zweite den Eintrag → rot. ``absence_status``
hatte bis dahin GAR KEINE Isolation.

Der iCal-Token-Store ist eine Sonderform: KEINE Modul-Konstante, sondern eine
``SP5Database``-Methode über ``_paths.data_dir()`` — ohne Umlenkung schreiben die
Feed-Tests echte Tokens in das getrackte ``data/ical_tokens.json``.
"""

from sp5api.dependencies import get_db
from sp5api.routers import absences as ab
from sp5api.routers import recurring_shifts as rs


def test_recurring_leaves_a_pattern_without_cleanup():
    rs._write_all(
        [
            {
                "id": 1,
                "employee_id": 40,
                "shift_id": 1,
                "recurrence": "weekly",
                "day_of_week": 0,
                "valid_from": "2026-01-01",
                "valid_until": "2026-12-31",
            }
        ]
    )
    assert len(rs._read_all()) == 1


def test_recurring_store_starts_empty():
    assert rs._read_all() == []


def test_absence_status_leaves_an_entry_without_cleanup():
    ab._save_absence_status({"1": {"status": "approved", "reject_reason": ""}})
    assert ab._load_absence_status() == {"1": {"status": "approved", "reject_reason": ""}}


def test_absence_status_store_starts_empty():
    assert ab._load_absence_status() == {}


def test_ical_tokens_leaves_a_token_without_cleanup():
    get_db().create_ical_token(999)
    assert get_db().get_ical_token_for_employee(999) is not None


def test_ical_tokens_store_starts_empty():
    assert get_db()._load_ical_tokens() == {}
