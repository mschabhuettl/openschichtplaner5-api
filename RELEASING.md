# Releases

Releases laufen zweistufig: `prepare-release.yml` setzt Version und CHANGELOG
und pusht das annotierte Tag; das tag-getriebene `release.yml` übernimmt das
Publish nach PyPI (Trusted Publishing).

## Ablauf

1. **Änderungen pflegen:** Jede release-relevante Änderung unter
   `## [Unreleased]` in `CHANGELOG.md` dokumentieren. Der Release-Workflow
   bricht ab, wenn die Sektion fehlt oder leer ist.

2. **Trockenlauf prüfen:**

   ```bash
   gh workflow run prepare-release.yml -f bump=minor -f dry_run=true
   ```

   `bump`: `patch`/`minor`/`major`; alternativ `-f version=X.Y.Z` (hat
   Vorrang). Das Step-Summary zeigt geplante Version, Changelog-Auszug und
   `git diff` — es wird nichts gepusht.

3. **Release ausführen:**

   ```bash
   gh workflow run prepare-release.yml -f bump=minor -f dry_run=false
   ```

   Der Workflow committet Version + CHANGELOG auf `main` (inkl.
   `_API_VERSION`-Fallback in `sp5api/main.py`), pusht das annotierte Tag
   `vX.Y.Z` und stößt `release.yml` auf dem Tag-Ref an.

4. **Publish (automatisch):** `release.yml` baut sdist + wheel und
   veröffentlicht auf PyPI.

5. **Downstream-Pins:** `openschichtplaner5` zieht den PyPI-Pin täglich
   automatisch nach (`update-pins.yml`). Direkt nach einem Release manuell
   anstoßen — `expected_api` wartet die PyPI-CDN-Propagation ab:

   ```bash
   gh workflow run update-pins.yml -R mschabhuettl/openschichtplaner5 -f expected_api=X.Y.Z
   ```

Eingehend: erscheint eine neue Library-Version, zieht der hiesige
`update-pins.yml` den `libopenschichtplaner5`-Pin in Dockerfile und
docker-compose täglich nach (manuell: `-f expected_lib=X.Y.Z`).
