# Releases

Releases laufen zweistufig: `prepare-release.yml` setzt Version und CHANGELOG
und pusht das annotierte Tag; das tag-getriebene `release.yml` veröffentlicht
**ein Tag auf alle Kanäle**:

| Kanal | Inhalt |
|---|---|
| **PyPI** | sdist + wheel, Trusted Publishing (OIDC, keine Tokens) |
| **ghcr.io** | das Standalone-Service-Image (Dockerfile-Default-Stage), multi-arch (amd64+arm64), Tags volle Version / Minor / `latest` |
| **GitHub-Release** | Body = die geschnittene CHANGELOG-Sektion; Assets = wheel + sdist + SBOM |

Zusätzlich verpflichtend und automatisch: **Build-Provenance-Attestation**
(`actions/attest-build-provenance`) für die Release-Assets **und** das Image,
sowie ein **SPDX-SBOM** je Image (`anchore/sbom-action`) als Release-Asset und
als SBOM-Attestation (`actions/attest-sbom`). Verifizierbar mit
`gh attestation verify <asset|oci://…> --owner mschabhuettl`.

Optional (ohne neue Secrets, Default aus): **cosign keyless** (OIDC) — per
Repo-Variable `ENABLE_COSIGN=true` einschalten
(`gh variable set ENABLE_COSIGN -b true`). Begründung: die verpflichtende
Build-Provenance ist bereits Sigstore-keyless signiert; cosign ist optionales
Opt-in.

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
   veröffentlicht auf **PyPI**, baut + pusht das Service-Image multi-arch nach
   **ghcr** (Tags Version/Minor/`latest`) und legt das **GitHub-Release** mit
   Changelog-Body und wheel/sdist/SBOM als Assets an — inkl.
   Attestation + SBOM (s. o.).

5. **Downstream-Pins:** `openschichtplaner5` zieht den PyPI-Pin täglich
   automatisch nach (`update-pins.yml`). Direkt nach einem Release manuell
   anstoßen — `expected_api` wartet die PyPI-CDN-Propagation ab:

   ```bash
   gh workflow run update-pins.yml -R mschabhuettl/openschichtplaner5 -f expected_api=X.Y.Z
   ```

Eingehend: erscheint eine neue Library-Version, zieht der hiesige
`update-pins.yml` den `libopenschichtplaner5`-Pin in Dockerfile und
docker-compose täglich nach (manuell: `-f expected_lib=X.Y.Z`).
