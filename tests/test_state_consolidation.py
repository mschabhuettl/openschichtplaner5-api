"""C2: Laufzeit-State-Konsolidierung (ROADMAP §C.2).

state_dir()/state_path() bündeln den Laufzeit-State in EIN injizierbares
Verzeichnis (SP5_STATE_DIR, Default backend_dir()/data) und migrieren
Altbestände aus backend/api/data bzw. backend/api verlustfrei.
"""

import importlib
import os

import pytest


@pytest.fixture(autouse=True)
def _restore_env():
    """Stelle SP5_BACKEND_DIR/SP5_STATE_DIR + das _paths-Modul danach wieder her,
    damit der globale env-Eingriff keine anderen Tests beeinflusst."""
    saved = {k: os.environ.get(k) for k in ("SP5_BACKEND_DIR", "SP5_STATE_DIR")}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    import sp5api._paths as p

    importlib.reload(p)


def _paths(backend, state=None):
    os.environ["SP5_BACKEND_DIR"] = str(backend)
    if state is not None:
        os.environ["SP5_STATE_DIR"] = str(state)
    else:
        os.environ.pop("SP5_STATE_DIR", None)
    import sp5api._paths as p

    return importlib.reload(p)


def test_default_state_dir_is_backend_data(tmp_path):
    p = _paths(tmp_path)
    assert p.state_dir() == os.path.join(str(tmp_path), "data")
    assert os.path.isdir(p.state_dir())


def test_explicit_state_dir_override(tmp_path):
    target = tmp_path / "mnt" / "sp5state"
    p = _paths(tmp_path, state=target)
    assert p.state_dir() == str(target)
    assert os.path.isdir(str(target))


def test_migrates_legacy_api_data(tmp_path):
    # Altbestand unter backend/api/data anlegen
    legacy_dir = tmp_path / "api" / "data"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "recurring_shifts.json").write_text('[{"id": "x"}]')

    p = _paths(tmp_path)
    new = p.state_path("recurring_shifts.json")

    assert new == os.path.join(p.state_dir(), "recurring_shifts.json")
    assert os.path.exists(new)
    assert not (legacy_dir / "recurring_shifts.json").exists()  # verschoben
    with open(new) as f:
        assert "x" in f.read()


def test_migrates_legacy_api_root(tmp_path):
    # Altbestand direkt unter backend/api (z. B. notifications.json)
    legacy_dir = tmp_path / "api"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "notifications.json").write_text('{"items": []}')

    p = _paths(tmp_path)
    new = p.state_path("notifications.json")
    assert os.path.exists(new)
    assert not (legacy_dir / "notifications.json").exists()


def test_no_migration_when_target_exists(tmp_path):
    p = _paths(tmp_path)
    new = p.state_path("availability.json")
    with open(new, "w") as f:
        f.write('{"current": true}')
    # Altbestand existiert ebenfalls — darf den neuen NICHT überschreiben
    legacy_dir = tmp_path / "api" / "data"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "availability.json").write_text('{"current": false}')

    again = p.state_path("availability.json")
    with open(again) as f:
        assert '"current": true' in f.read()
