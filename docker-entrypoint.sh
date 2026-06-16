#!/bin/sh
# ==============================================================================
# openschichtplaner5-api — Entrypoint
#
# Behebt die häufigste Betriebsursache für „Interner Serverfehler" beim Speichern:
# das gemountete DBF-Daten-Verzeichnis gehört dem Host-Benutzer, der Container
# läuft aber als anderer (non-root) Benutzer → Schreibzugriffe scheitern (EACCES).
#
# Wenn der Container als root startet (Standard), läuft die App anschließend als
# *Eigentümer des Daten-Verzeichnisses* — so kann sie die bind-gemounteten
# DBF-Dateien schreiben, OHNE deren Host-Eigentümer zu ändern. Die mutablen
# State-Verzeichnisse (Named Volumes) werden auf denselben Benutzer angeglichen.
# Wird der Container bereits als non-root gestartet (z. B. `--user`), übernimmt
# der Aufrufer die Verantwortung und der Entrypoint startet die App direkt.
# ==============================================================================
set -e

DATA_DIR="${SP5_DB_PATH:-/app/data}"

if [ "$(id -u)" = "0" ]; then
  uid="$(stat -c '%u' "$DATA_DIR" 2>/dev/null || echo 1001)"
  gid="$(stat -c '%g' "$DATA_DIR" 2>/dev/null || echo 1001)"
  # Root-owned (z. B. frisches leeres Named Volume) → bundled sp5-User + Ownership.
  if [ "$uid" = "0" ]; then uid=1001; gid=1001; fi
  # Beschreibbar machen (best effort; ein read-only gemountetes Daten-Verzeichnis
  # bleibt read-only — die App meldet das dann klar statt mit opakem 500).
  for d in "$DATA_DIR" /app/backend/data /app/backend/api /app/backend/backups; do
    [ -d "$d" ] && chown -R "$uid:$gid" "$d" 2>/dev/null || true
  done
  exec gosu "$uid:$gid" "$@"
fi

exec "$@"
