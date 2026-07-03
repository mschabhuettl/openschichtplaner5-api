"""Regression P-IMPROVE-ALL: Root-Statikdateien der SPA (sw.js, manifest.json,
Icons) müssen mit korrektem MIME-Typ ausgeliefert werden — der pauschale
index.html-Fallback lieferte für /sw.js text/html, womit die Service-Worker-
Registrierung in jedem Browser-Load fehlschlug (Konsole: „The script has an
unsupported MIME type ('text/html')")."""

import pytest
from fastapi import HTTPException


def _make_dist(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>SPA</html>")
    (dist / "sw.js").write_text("self.addEventListener('fetch', () => {});")
    (dist / "manifest.json").write_text("{}")
    (dist / ".versteckt").write_text("nein")
    sub = dist / "assets"
    sub.mkdir()
    (sub / "chunk.js").write_text("// via /assets-Mount, nicht via Fallback")
    return dist


class TestResolveRootStatic:
    def test_findet_root_dateien(self, tmp_path):
        from sp5api.main import _resolve_root_static

        dist = _make_dist(tmp_path)
        assert _resolve_root_static(str(dist), "sw.js") == str(dist / "sw.js")
        assert _resolve_root_static(str(dist), "manifest.json") == str(
            dist / "manifest.json"
        )

    def test_lehnt_traversal_und_verstecktes_ab(self, tmp_path):
        from sp5api.main import _resolve_root_static

        dist = _make_dist(tmp_path)
        assert _resolve_root_static(str(dist), "../dist/sw.js") is None
        assert _resolve_root_static(str(dist), "..\\sw.js") is None
        assert _resolve_root_static(str(dist), ".versteckt") is None
        assert _resolve_root_static(str(dist), "assets/chunk.js") is None
        assert _resolve_root_static(str(dist), "") is None

    def test_fehlende_datei_faellt_zurueck(self, tmp_path):
        from sp5api.main import _resolve_root_static

        dist = _make_dist(tmp_path)
        assert _resolve_root_static(str(dist), "gibtsnicht.js") is None


class TestSpaFallbackResponse:
    def test_sw_js_kommt_als_javascript(self, tmp_path, monkeypatch):
        import sp5api.main as main_module

        dist = _make_dist(tmp_path)
        monkeypatch.setattr(main_module, "_FRONTEND_DIST", str(dist))

        resp = main_module._spa_fallback_response("sw.js")
        assert resp.path == str(dist / "sw.js")
        assert "javascript" in (resp.media_type or "")

    def test_unbekannte_route_liefert_index(self, tmp_path, monkeypatch):
        import sp5api.main as main_module

        dist = _make_dist(tmp_path)
        monkeypatch.setattr(main_module, "_FRONTEND_DIST", str(dist))

        resp = main_module._spa_fallback_response("dienstplan")
        assert resp.path == str(dist / "index.html")

    def test_api_tippfehler_bleibt_404(self, tmp_path, monkeypatch):
        import sp5api.main as main_module

        dist = _make_dist(tmp_path)
        monkeypatch.setattr(main_module, "_FRONTEND_DIST", str(dist))

        with pytest.raises(HTTPException) as exc:
            main_module._spa_fallback_response("api/gibtsnicht")
        assert exc.value.status_code == 404
