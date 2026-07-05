"""Regressionsschutz: der Recurring-Shifts-Store ist pro Test isoliert.

Ohne die autouse-Fixture ``_recurring_store_in_tmp`` (conftest) teilen sich alle
Tests eine einzige ``recurring_shifts.json`` — ``_RECURRING_FILE`` ist eine
import-fixe Modul-Konstante auf einen geteilten Pfad. Ein Muster, das ein Test
hinterlässt, war dann für spätere Tests sichtbar: Ursache des seltenen, reihenfolge-
abhängigen Voll-Lauf-Flakes in ``test_recurring_shifts::test_delete_existing``,
dessen Endzusicherung ``GET /api/shifts/recurring == []`` lautet.

Die beiden Tests laufen in Definitionsreihenfolge: der erste schreibt ein Muster
und räumt NICHT auf, der zweite verlangt einen leeren Store. Mit Isolation sind
beide grün; entfernt man die Fixture, sieht der zweite das Muster → rot.
"""

from sp5api.routers import recurring_shifts as rs


def test_leaves_a_pattern_without_cleanup():
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


def test_store_starts_empty():
    assert rs._read_all() == []
