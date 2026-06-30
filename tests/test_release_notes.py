"""Tests for GET /api/release-notes (the „Was ist neu?" page source).

P2-8 (Punkt 40): „Was ist neu" war leer, weil das Image keine CHANGELOG.md an
``backend_dir()/../CHANGELOG.md`` mitlieferte → der Endpunkt fiel auf
„No changelog available." zurück. Diese Tests sichern das Endpunkt-Verhalten:
liegt die Datei dort, wird ihr Inhalt geliefert; fehlt sie, der Platzhalter.
Das Mitliefern der Datei im Image deckt der Dockerfile-COPY ab.
"""

import secrets

from starlette.testclient import TestClient

import sp5api.routers.misc as misc


def _client(monkeypatch, backend_dir_path):
    from sp5api.main import _sessions, app

    monkeypatch.setattr(misc, "backend_dir", lambda: str(backend_dir_path))
    tok = secrets.token_hex(20)
    _sessions[tok] = {"ID": 991, "NAME": "rn_admin", "role": "Admin", "ADMIN": True, "RIGHTS": 255}
    client = TestClient(app, raise_server_exceptions=False)
    client.headers["X-Auth-Token"] = tok
    return client, tok


def test_release_notes_returns_changelog_when_present(monkeypatch, tmp_path):
    from sp5api.main import _sessions

    backend = tmp_path / "backend"
    backend.mkdir()
    # Endpoint reads backend_dir()/../CHANGELOG.md  → tmp_path/CHANGELOG.md
    changelog = "# Changelog\n\n## [1.2.3] - 2026-06-30\n\n### Added\n\n- Tolles Feature\n"
    (tmp_path / "CHANGELOG.md").write_text(changelog, encoding="utf-8")

    client, tok = _client(monkeypatch, backend)
    try:
        res = client.get("/api/v1/release-notes")
        assert res.status_code == 200
        assert res.json()["content"] == changelog
        assert "1.2.3" in res.json()["content"]
    finally:
        _sessions.pop(tok, None)


def test_release_notes_placeholder_when_missing(monkeypatch, tmp_path):
    from sp5api.main import _sessions

    backend = tmp_path / "backend"
    backend.mkdir()
    # No CHANGELOG.md created → endpoint falls back to the placeholder.
    client, tok = _client(monkeypatch, backend)
    try:
        res = client.get("/api/v1/release-notes")
        assert res.status_code == 200
        assert res.json()["content"] == "No changelog available."
    finally:
        _sessions.pop(tok, None)
