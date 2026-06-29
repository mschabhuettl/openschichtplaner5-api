"""Regression (P2-3 / P2-7): die ORM-/Firmen-SQLite-DB muss im beschreibbaren
State-Verzeichnis liegen (``state_path`` / ``SP5_STATE_DIR``), NICHT neben den
DBF-Daten.

Bug: ``companies._get_orm_session`` und ``orm_mirror._get_orm_engine`` leiteten den
Pfad als ``dirname(SP5_DB_PATH)/sp5_orm.db`` ab. Im Container ist ``SP5_DB_PATH=
/app/data`` (gemountetes, dem App-User gehörendes Volume), dessen Elternverzeichnis
``/app`` aber root gehört → ``init_db`` scheitert mit EACCES → HTTP 500 in der
Firmenverwaltung und „Fehler beim Laden" im ORM-Spiegel. Kein Test deckte die echte
Pfadableitung ab (alle mockten ``_get_orm_session``) — daher dieser Test.
"""

import os


def test_orm_db_created_in_writable_state_dir(tmp_path, monkeypatch):
    from sp5api.routers import companies, orm_mirror

    state = tmp_path / "state"
    monkeypatch.setenv("SP5_STATE_DIR", str(state))
    # SP5_DB_PATH bewusst woanders: die ORM-DB darf NICHT hierher abgeleitet werden.
    monkeypatch.setenv("SP5_DB_PATH", str(tmp_path / "dbf_data"))

    # Companies-Router: Session bauen → init_db legt die Datei im State-Dir an.
    session, engine = companies._get_orm_session()
    try:
        assert (state / "sp5_orm.db").exists(), (
            "ORM-DB nicht im beschreibbaren State-Verzeichnis angelegt"
        )
        # Nicht neben/oberhalb der DBF-Daten (alte, kaputte Ableitung).
        assert not (tmp_path / "sp5_orm.db").exists()
        assert str(state) in str(engine.url)
    finally:
        session.close()

    # ORM-Spiegel-Router teilt denselben Pfad.
    eng2 = orm_mirror._get_orm_engine()
    assert os.path.dirname(str(eng2.url).replace("sqlite:///", "")) == str(state)
