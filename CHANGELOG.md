# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.23.0] - 2026-07-02

### Changed

- **Tauschbörse verlangt einen tauschbaren Dienst beider Mitarbeiter.**
  Tauschanträge (Planer- und Self-Service-Route) werden nur noch angelegt, wenn
  sowohl Antragsteller als auch Partner am jeweils eigenen Datum einen Dienst
  haben (sonst 400 mit klarer Meldung). Die Genehmigung prüft dies erneut und
  verweigert mit 409, falls ein Dienst seit der Antragstellung entfernt wurde —
  bisher löschte die Genehmigung eines einseitigen Antrags den vorhandenen
  Dienst ersatzlos.

## [1.22.0] - 2026-06-30

### Added

- `GET /api/health` liefert im `db`-Block jetzt zusätzlich `employees` (Mitarbeiterzahl
  der verbundenen Datenbank) für die „Mitarbeiter"-Kachel des System-Health-Dashboards.
  Der Wert stammt aus dem bereits im Health-Check berechneten `get_stats()` (zuvor
  verworfen); Pfad/Fehlerdetails bleiben weiterhin bewusst unexponiert (öffentlicher
  Endpunkt).

## [1.21.0] - 2026-06-30

### Added

- **Druckbarer „Urlaubsantrag" (`GET /api/reports/vacation-request`, PDF).** Erzeugt das
  Urlaubsantrags-Formular des Originals für einen Mitarbeiter und einen Zeitraum
  (`employee_id`, `from_date`, `to_date`, optional `leave_type_id`): Antragsteller,
  Abwesenheitsart, Zeitraum, beantragte Tage und Antragsdatum, dazu Genehmigt-/
  Abgelehnt-Ankreuzfeld und die Unterschriftszeilen „Datum, Unterschrift Antragsteller/
  Vorgesetzter" (Original-Strings aus `SP5Res.dll`). Damit bekommt die Urlaubsgenehmigung
  das papiergebundene Pendant des Originals; der interaktive Genehmigen/Ablehnen-Workflow
  bleibt unverändert. Unbekannter Mitarbeiter → 404, ungültiger/umgekehrter Zeitraum → 400.

## [1.20.2] - 2026-06-29

### Fixed

- **„Wiederkehrende Schichten" war im Frontend nicht ladbar** (Seite blieb weiß, auch
  nach Neuladen). Ursache war ein Vertrags-Bruch zwischen Frontend und API: die Liste
  `GET /api/shifts/recurring` lieferte ein Objekt `{patterns, total}`, das Frontend
  erwartet aber ein Array und rief `.map()` darauf auf → Render-Absturz schon beim
  leeren Datenstand. Zusätzlich verlangte das Anlegen Felder, die das UI gar nicht
  schickt (`shift_type`, `start_time`, `end_time`), `generate` lieferte `generated`
  statt des erwarteten `created`, und die IDs waren UUID-Strings statt Ganzzahlen.
  Die Endpunkte folgen jetzt durchgängig dem Frontend-Vertrag: Liste/Anlegen liefern
  angereicherte Objekte (`employee_name`/`shift_name`/`shift_short`), ein Muster
  referenziert die Schicht nur über `shift_id` (die Schicht trägt ihre eigenen
  Start-/Endzeiten — die im Generieren ohnehin ungenutzten Zeitfelder entfielen),
  `generate` liefert `{created, skipped}`, IDs sind fortlaufende Ganzzahlen. Der
  Gruppenfilter wirkt nun über die Gruppenmitgliedschaft. `tests/test_recurring_shifts.py`
  auf den funktionierenden Vertrag umgestellt (vorher grün gegen den kaputten Vertrag —
  klassisches „Tests grün, App kaputt", weil kein Test die Frontend-↔-API-Grenze querte).

## [1.20.1] - 2026-06-29

### Fixed

- **Firmenverwaltung und ORM-Spiegel lieferten im Container HTTP 500** („unable to
  open database file"). Die ORM-/Firmen-SQLite-DB (`sp5_orm.db`) wurde als
  `dirname(SP5_DB_PATH)/sp5_orm.db` abgeleitet. Im Container ist `SP5_DB_PATH=
  /app/data` (das dem App-User gehörende Volume), dessen Elternverzeichnis `/app`
  jedoch root gehört → `init_db` scheiterte mit EACCES, jeder Aufruf von
  `GET /api/companies` und `GET /api/admin/orm/status` endete in 500. Beide Stellen
  (`companies._get_orm_session`, `orm_mirror._get_orm_engine`) nutzen jetzt das
  konsolidierte, beschreibbare State-Verzeichnis (`state_path`/`SP5_STATE_DIR`,
  Default `SP5_BACKEND_DIR/data`) — dieselbe veränderliche-Zustand-Konvention wie der
  Rest der App. Regressionstest `tests/test_orm_db_path.py` (die echte Pfadableitung
  war bisher von keinem Test abgedeckt, da alle `_get_orm_session` mockten).

### Changed

- Bündelt **libopenschichtplaner5 1.23.0**: Benutzerrollen/-rechte werden jetzt korrekt
  aus dem `5USER.RIGHTS`-Modus aufgelöst. Echte Konten mit vollen/differenzierten
  Schreibrechten (RIGHTS 0/2) wurden bisher fälschlich als „Leser" angezeigt; zudem
  ist die **Schreib-Durchsetzung** nun an den Modus gekoppelt — ein Nur-Lese-Konto
  (RIGHTS 1/3) erhält über `get_current_user`/`require_write` keine Schreibrechte mehr,
  selbst wenn alte W*-Flags im Satz gesetzt sind. Kein API-Code-Change (Auflösung
  liegt in der Bibliothek), aber sichtbare Verhaltenskorrektur bei Rolle/Durchsetzung.

## [1.19.1] - 2026-06-29

### Changed

- Bündelt **libopenschichtplaner5 1.22.1** (Floor und Image-Pin auf `>=1.22.1`
  bzw. `==1.22.1` angehoben). Diese lib-Version behebt einen Datenintegritäts-Bug:
  nebenläufige Schreibvorgänge vergaben unter Last doppelte Datensatz-IDs (ID wurde
  als `max(ID)+1` außerhalb des Append-Locks berechnet), wodurch ID-adressierte
  Updates/Deletes den falschen Satz treffen und Einträge „vertauschen" konnten. Kein
  API-Code-Change — reiner Abhängigkeits-Pin, damit das API-Image den Fix enthält.

## [1.19.0] - 2026-06-29

### Added

- Sonderdienst „keine Arbeitszeitzuschläge berechnen": `POST /api/einsatzplan` und
  `PUT /api/einsatzplan/{id}` akzeptieren jetzt das Feld `noextra` (bool), das nach
  5SPSHI.NOEXTRA geschrieben wird (Spec 3.8.3 Nr. 13). `GET /api/einsatzplan` liefert
  es als `noextra` zurück. Wirkt für freie Sonderdienste ohne Schicht-Referenz.
  Erfordert libopenschichtplaner5 >= 1.22.0.

## [1.18.0] - 2026-06-29

### Added

- Urlaubssperre bearbeiten: `PUT /api/holiday-bans/{ban_id}` ändert eine bestehende
  Sperre (5HOBAN). Nur die übergebenen Felder (`group_id`, `start_date`, `end_date`,
  `reason`) werden geschrieben. Unbekannte ID → 404, ungültiges Datumsformat → 422,
  Ende vor Start (auf dem zusammengeführten Stand geprüft) → 400. Erfordert
  libopenschichtplaner5 >= 1.21.0.

## [1.17.0] - 2026-06-28

### Added

- Gekennzeichneten Zeitraum bearbeiten: `PUT /api/periods/{period_id}` ändert einen
  bestehenden Zeitraum (5PERIO). Nur die übergebenen Felder (`group_id`, `start`,
  `end`, `description`, `color`) werden geschrieben. Unbekannte ID → 404, ungültiges
  Datumsformat → 422, Ende vor Start (auf dem zusammengeführten Stand geprüft) → 400.
  Erfordert libopenschichtplaner5 >= 1.20.0.

## [1.16.0] - 2026-06-28

### Added

- Manuelle Kontobuchung bearbeiten: `PUT /api/bookings/{booking_id}` ändert eine
  bestehende Buchung. Nur die übergebenen Felder (`date`, `type`, `value`, `note`)
  werden geschrieben, der Rest bleibt unverändert. Unbekannte ID → 404, ungültiges
  Datumsformat → 400. `WPAST` greift sowohl auf das bestehende als auch (bei
  Datumswechsel) auf das neue Datum. Erfordert libopenschichtplaner5 >= 1.19.0.

## [1.15.0] - 2026-06-28

### Added

- Monatsabschluss-Report (`GET /api/reports/monthly`, PDF): optionale Query-Parameter
  `title` (eigener Berichtstitel in der Kopfzeile, max. 120) und `footer` (eigener
  Fußtext, max. 200). Ohne Angabe bleiben „Monatsabschluss-Report" bzw. die
  Standard-Fußzeile. Nicht in Latin-1 darstellbare Zeichen werden ersetzt (fpdf2-
  Kernfont), sodass Nutzereingaben nie einen Fehler auslösen.

## [1.14.0] - 2026-06-28

### Added

- Schichtmodell-Zuordnung über einen Zeitraum: `POST /api/shift-cycles/assign`
  akzeptiert jetzt ein optionales `end_date` (`YYYY-MM-DD`), das die Zuordnung
  befristet (5CYASS.END). Die Zyklus-Expansion erzeugt danach keine Dienste mehr;
  ohne Ende gilt die Zuordnung weiter offen. Ein Ende vor dem Start wird mit 400
  abgelehnt. Erfordert libopenschichtplaner5 >= 1.18.0.

## [1.13.0] - 2026-06-28

### Added

- Schicht- und Abwesenheitsarten: Fettschrift-Flag `BOLD` (0/1) in den Modellen
  `ShiftCreate`/`ShiftUpdate` und `LeaveTypeCreate`/`LeaveTypeUpdate`. Wird an die
  Bibliothek weitergereicht (5SHIFT.BOLD / 5LEAVT.BOLD); beim Update wird ein
  explizites `BOLD=0` nicht als „nicht gesetzt" verworfen. Erfordert
  libopenschichtplaner5 >= 1.17.0.

## [1.12.0] - 2026-06-28

### Added

- Benutzerverwaltung: granulare Schreibrechte beim Anlegen/Bearbeiten setzbar.
  `POST /api/users` und `PUT /api/users/{id}` akzeptieren ein optionales
  `permissions`-Objekt mit 5USER-Flag-Schlüsseln (WDUTIES, WABSENCES,
  WOVERTIMES, WNOTES, WDEVIATION, WCYCLEASS, WSWAPONLY, WPAST, ADDEMPL, BACKUP);
  gesetzte Flags überschreiben die rollenbasierten Defaults. Unbekannte
  Schlüssel werden mit 422 abgelehnt. Das serverseitige Enforcement bestand
  bereits — bisher waren die Flags nur grob über die Rolle vergebbar. Erfordert
  lib ab 1.16.0.

## [1.11.0] - 2026-06-28

### Added

- Abwesenheit anlegen (`POST /api/absences`): optionales Feld `comment` (max.
  125 Zeichen). Faithful zum Original (Abwesenheits-Eingabe „nicht ganztägig"):
  Der Kommentartext wird als Dienstplan-Kommentar in `5NOTE` gespeichert (am
  selben Datum/Mitarbeiter, HTML-escaped) — der Abwesenheitssatz `5ABSEN` selbst
  hat kein Textfeld. Das Schreiben der Notiz ist best-effort: schlägt es fehl,
  wird die Abwesenheit dennoch angelegt und eine Warnung zurückgegeben.

## [1.10.0] - 2026-06-28

### Added

- Admin-Impersonation („Als Benutzer ansehen"): Ein Admin kann die Anwendung
  vorübergehend als ein anderer Benutzer ansehen. `POST /api/auth/impersonate/{user_id}`
  (nur Admin) startet, `POST /api/auth/impersonate/stop` beendet. Es ist **kein** neuer
  Login/Token — die Admin-Session bleibt unverändert; nur der Autorisierungs-Principal
  wird auf die Ziel-Identität abgebildet, sodass **ausschließlich** dessen Rolle/Rechte/
  Sichtbarkeit gelten (nie mehr als der Admin). **Nur lesend**: während aktiver
  Impersonation sind alle schreibenden Anfragen zentral mit `403` gesperrt. **Nicht
  verschachtelbar** und **serverseitig auditiert** (`ACT_AS_START`/`ACT_AS_END` mit dem
  echten Admin als Actor). Der Login-/Digest-Prüfpfad bleibt unberührt. Erfordert
  `libopenschichtplaner5 >= 1.15.0` (`get_user_identity`).

## [1.9.1] - 2026-06-28

### Fixed

- Konflikterkennung läuft jetzt durchgängig nur auf der Ist-Ebene. Ein Sollplan-Ziel
  (`5MASHI.TYPE=1`) ist eine geplante Vorgabe und kein tatsächlicher Dienst; eine
  Soll-/Ist-Überlagerung am selben Tag ist die normale Zwei-Ebenen-Ansicht und kein
  Konflikt. Betrifft die über `get_schedule_conflicts` ausgelieferten Hinweise
  (`/api/schedule/...`, Monatsbericht) — eine Soll-Schicht neben einem Krankenstand
  erscheint nicht mehr als `shift_and_absence`-Konflikt — sowie `_detect_conflicts`
  in `/api/conflicts/report`, wo Doppelbelegung/Overlap und Unterbesetzung nur noch
  Ist-Schichten auswerten. Echte Konflikte (zwei Ist-Schichten am selben Tag) bleiben
  erkannt. Erfordert `libopenschichtplaner5 >= 1.14.4`.

## [1.9.0] - 2026-06-28

### Changed

- `GET /api/schedule` reicht für Abwesenheiten jetzt die Teiltags-Felder `interval`,
  `start_time` und `end_time` durch (aus `get_schedule`), sodass der Dienstplan
  Teiltags-Abwesenheiten erkennen und beim Wiederherstellen/Verschieben die
  Granularität erhalten kann. Erfordert `libopenschichtplaner5 >= 1.14.0`.

## [1.8.0] - 2026-06-16

### Added

- `GET /api/extracharges/by-day`: Zeitzuschläge je Tag (Spec 3.8) — je
  (Mitarbeiter, Tag, Zuschlag) eine Zeile mit Stunden > 0, für einen Monat
  (`year`+`month`) oder einen freien Zeitraum (`from`/`to`), optional je
  `employee_id`. Die Summe der Tageszeilen je Regel entspricht
  `/api/extracharges/summary`. Erfordert libopenschichtplaner5 >= 1.13.0.

## [1.7.0] - 2026-06-16

### Added

- `POST /api/absences` warnt jetzt, wenn die Abwesenheit in einen Sperrzeitraum
  (Urlaubssperre, 5HOBAN) einer Gruppe des Mitarbeiters fällt (Spec R5.10-5). Die
  Warnung wird wie die bestehenden Konflikt-/Feiertagshinweise im `warnings`-Feld
  der Antwort geliefert; die Eintragung bleibt möglich (weiche Warnung, keine
  Sperre). Der Geltungsbereich („alle" vs. „nur anspruchsgebunden", R5.10-7) wird
  bewusst nicht eingeschränkt, da das `RESTRICT`-Enum aus dem Originalmaterial
  nicht eindeutig bestimmbar ist — es wird konservativ für jede Abwesenheitsart
  gewarnt.
- `POST /api/periods` akzeptiert jetzt eine optionale `color` (`#RRGGBB`) für
  gekennzeichnete Zeiträume (5PERIO, R5.10-10); sie wird als COLORREF gespeichert
  und von `GET /api/periods` als Hex zurückgegeben. Ohne Angabe bleibt der
  bisherige Default.

## [1.6.1] - 2026-06-16

### Changed

- Release-Automatik vervollständigt: ein Versions-Tag veröffentlicht jetzt
  zusätzlich zum PyPI-Paket automatisch das Standalone-Service-Image nach
  `ghcr.io/mschabhuettl/openschichtplaner5-api` (multi-arch amd64+arm64, Tags
  volle Version / Minor / `latest`) und legt ein GitHub-Release mit dem
  Changelog-Auszug sowie wheel, sdist und einem SPDX-SBOM als Assets an.
  Release-Assets und Image tragen eine Build-Provenance-Attestation; je Image
  wird ein SBOM erzeugt und attestiert. Optionale cosign-Signierung über die
  Repo-Variable `ENABLE_COSIGN`.

## [1.6.0] - 2026-06-16

### Fixed

- **Login über reines HTTP nutzbar:** Das Session-Cookie wird nur noch `Secure`
  gesetzt, wenn die Anfrage tatsächlich über HTTPS kommt (X-Forwarded-Proto bzw.
  Request-Scheme; `SP5_COOKIE_SECURE=true|false` als Override). Browser verwerfen
  ein `Secure`-Cookie über reines HTTP auf jedem Nicht-localhost-Host; da die SPA
  die Sitzung ausschließlich im HttpOnly-Cookie hält, schlug der Login auf üblichen
  Self-Hosting-/Portainer-Deployments faktisch fehl (200 beim Login, danach 401).
- **Kein opaker 500 mehr bei Schreibfehlern:** Datei-/Rechte-Fehler (EACCES/EROFS/
  ENOSPC) liefern eine klare, spezifische Meldung + Log statt eines generischen 500 —
  zentral über `describe_write_error`, genutzt von `_sanitize_500` und einem globalen
  `OSError`-Handler (deckt auch nicht selbst abgesicherte Pfade wie `/api/wishes` ab).
- **Schreibrechte im Container** (Entrypoint): Der Container startet als root,
  gleicht die Schreibrechte am gemounteten Daten-Verzeichnis an und führt die App
  via `gosu` als dessen Eigentümer (sonst uid 1001) aus — behebt „Interner
  Serverfehler" beim Speichern (Umplanen/Wünsche/neuer Mitarbeiter) bei
  non-root-Container × host-eigenem Bind-Mount. Auto-Backups in `SP5_BACKUP_DIR`.

### Added

- Datenschutzsichere Login-Diagnostik: bei Fehl-Login wird der Grund geloggt
  (Benutzer (nicht) gefunden, Digest-Format, bcrypt) — nie das Passwort.
- CI-Job „Container write-permission": startet das Image mit einem fremd-besessenen
  Daten-Verzeichnis und prüft, dass Schreibzugriffe gelingen.

### Changed

- Library-Untergrenze auf `libopenschichtplaner5>=1.12.0` (login_diagnostics);
  Docker-`LIB_SOURCE` auf `==1.12.0`.

### Removed

- D11: der wirkungslose `max_carry_forward_days`-Parameter (annual-close
  preview/execute) ist entfernt; Alt-Clients, die ihn noch senden, werden weiterhin
  fehlerfrei ignoriert.

## [1.5.0] - 2026-06-12

### Changed

- Library-Untergrenze auf `libopenschichtplaner5>=1.11.0` angehoben (reorder);
  Docker-`LIB_SOURCE` auf `==1.11.0`.

### Added

- `POST /api/reorder/{entity}` (entity = employees|shifts|groups|leave_types|
  workplaces): manuelle, programmweite Stammdaten-Sortierung (POSITION, Spec 5.1
  Nr. 4); Planer-Rolle; invalidiert den Listen-Cache. Erfordert lib >=1.11.0.

## [1.4.0] - 2026-06-12

### Changed

- Library-Untergrenze auf `libopenschichtplaner5>=1.10.0` angehoben
  (Anonymisierung, Sichtbarkeits-Scopes, Arbeitsplatz-Schreiben, schedule_type,
  RESTRICT-Grad); Docker-`LIB_SOURCE` auf `==1.10.0`.

### Added

- Einschränkungs-Grad (Spec 4.11): `POST /api/restrictions` nimmt `grade`
  (0=keine, 1=auf Anfrage, 2=nie; Vorgabe 2). Die Konflikt­prüfung beim
  Eintragen sperrt hart nur bei „nie" (409); „auf Anfrage" lässt die Eintragung
  mit `warning` zu, „keine" greift nicht.
- Laufzeit-State konsolidiert (ROADMAP §C.3): JSON-Stores, Queues und Zähler
  liegen jetzt unter EINEM injizierbaren Verzeichnis `SP5_STATE_DIR` (Default
  `backend/data`) statt verstreut über `backend/data`, `backend/api/data` und
  `backend/api`. Altbestände werden beim ersten Zugriff verlustfrei migriert —
  bestehende Deployments funktionieren unverändert weiter.
- Differenzierte Sichtbarkeit (Spec 9.5.3): Benutzer mit 5GRACC/5EMACC-Festlegung
  sehen in `/api/employees`, `/api/employees/{id}`, `/api/groups` und
  `/api/schedule` nur ihre zugänglichen Mitarbeiter/Gruppen (verborgene MA: 404).
  Admin/volle Rechte unverändert unbeschränkt.
- Optionaler Redis-Session-Store (ROADMAP §C.2): `SP5_SESSION_BACKEND=redis`
  (Default `memory`, unverändert) teilt Sessions über mehrere Worker via
  `SP5_REDIS_URL`. `redis` ist eine optionale Extra-Abhängigkeit; beide Backends
  (Memory/Redis) sind getestet (fakeredis).
- Arbeitsplatz im Dienstplan (Spec 6.4): `POST /api/schedule` akzeptiert
  `workplace_id`; neuer `POST /api/schedule/workplace` setzt den Arbeitsplatz
  eines bestehenden Eintrags; das Schedule-Ergebnis trägt `workplace_name`.
- Soll-/Istplan (Spec 4.12): `GET /api/schedule?plan=ist|soll|both` wählt die
  Plansicht; `POST /api/schedule` akzeptiert `schedule_type` (0=Ist, 1=Soll). Die
  Duplikat-/Überlappungsprüfung ist planart-bewusst (Soll- und Ist-Eintrag dürfen
  am selben Tag bestehen).
- Abwesenheits-Anonymisierung (Spec 9.5.2 Nr. 2.1, D-67): `/api/schedule`,
  `/api/schedule/day`, `/api/schedule/week` und der Dienstplan-Druck wenden den
  SHOWABS-Modus des angemeldeten Benutzers an — vollständig (0), anonymisiert (1,
  Einheitsdarstellung aus 5USETT) oder ausgeblendet (2). Admins sehen immer
  vollständig. Neuer `absence_visibility_mode`-Dependency; `/api/auth/me` liefert
  `showabs_mode`; `PUT /api/v1/users/{id}` akzeptiert das dreiwertige `SHOWABS`.

## [1.3.0] - 2026-06-12

### Fixed

- **Login restored.** The token issued by `/api/auth/login` now also works as a
  standard `Authorization: Bearer` header (previously only the cookie /
  X-Auth-Token were accepted, so API clients got 401). Login no longer rejects
  empty/short passwords, so original 5USER accounts authenticate with their
  existing (MD5) password. Shift-restriction conflict checks use the original
  day index (0=Mon..6=Sun, 7=holiday) instead of "0=all, 1=Mon..7=Sun (ISO)".

### Added

- Demo-user bootstrap: `admin` / `planer` / `leser` (password `Test1234`,
  roles Admin/Planer/Leser) are seeded on startup in dev mode or with
  `SP5_SEED_DEMO_USERS=1` (never in a default production deployment).
- `GET /api/schedule/eligible-replacements`: replacement candidates filtered by
  group, employment period, availability and shift restriction.

### Added

- `prepare-release` workflow (manual dispatch): bumps the version, cuts the
  `[Unreleased]` changelog section into a release section and pushes commit +
  annotated tag — the tag keeps driving the PyPI publish. Dry-run mode
  (default) only reports the planned changes. The workflow refuses to release
  when the `[Unreleased]` section is missing or empty. `RELEASING.md`
  documents the flow.

### Changed

- Docker builds install the library from PyPI by default
  (`libopenschichtplaner5[postgres]==1.7.0`, pinned); building against the
  development state remains possible via the `LIB_SOURCE` build argument.
- Tests resolve their data directory uniformly via `SP5_REAL_DB`, falling back
  to the bundled `tests/fixtures/` database.

### Documentation

- Standalone Docker operation (single container, DBF directory mounted to
  `/app/data`, configuration via `.env`) is documented in the README; PyPI is
  the recommended installation path.
- README slimmed down to the essentials; detailed guides (endpoints,
  permissions, environment reference) move to the
  [project wiki](https://github.com/mschabhuettl/openschichtplaner5-api/wiki).
- `docs/architecture.md` updated to the 1.2.0 state (route inventory,
  permission enforcement, current verification figures).

## [1.2.0] - 2026-06-11

Parity pass against the original Schichtplaner5 (spec chapter 3/9) plus the
follow-up cleanup: all Soll/Ist/demand figures now come from the `sp5lib`
calculation facade — the API no longer carries its own arithmetic.

### Added

- `GET /api/personnel-table` — the original "Personaltabelle" over a free
  evaluation period `from`/`to` (spec 3.9.2/3.9.3): standard columns per
  employee (actual/nominal hours, balance, paid absence, Sunday/holiday duty
  days, special duties) plus dynamic per-shift and per-leave-type columns;
  entitled leave types report taken/remaining for exact calendar years.
  Rate-limited 10/minute.
- `POST /api/leave-entitlements/forfeit` — cutoff-date forfeiture of remaining
  leave (spec 3.7.3, dialog 5.17) with `dry_run` preview. Admin only,
  rate-limited 5/minute.
- `GET /api/statistics` accepts a free evaluation period `from`/`to`
  (spec 3.9.1) in addition to year/month.
- Partial-day absences (`interval`) via `POST`/`PUT /api/absences` (spec 3.5),
  half holidays and following-year holidays via `POST`/`PUT /api/holidays`
  (spec 3.2), `keep_entitlements` flag on the annual-close routes (spec 3.7.2).
- Shift requests: `NOEXTRA` and up to three `STARTEND` work-time windows per
  day index incl. holiday index 7; `5SHDEM` demand accepts day index 7.
- **Granular 5USER permissions (spec 9.6)**: `/api/auth/me` exposes a
  `permissions` object and every write route enforces its flag — `WDUTIES`
  (schedule), `WABSENCES`, `WOVERTIMES` (bookings), `WNOTES`, `WDEVIATION`,
  `WCYCLEASS`, `WSWAPONLY` (swap), `ADDEMPL` (opt-in employee creation).
  The built-in `Admin` account (ID 251) and the last remaining administrator
  are protected against demotion/deletion.
- **WPAST past-write protection on bulk routes**: `WPAST=0` now also blocks
  past dates on `POST /api/schedule/bulk` (per entry), `/bulk-group`,
  `/copy-week`, `/swap` (per date), the Einsatzplan writes
  (`POST/PUT/DELETE /api/einsatzplan*`, deviation) and the booking writes
  (`POST/DELETE /api/bookings*`) — for ID-based updates/deletes the date of
  the stored record counts.
- Production multi-stage Docker image (slim runtime, non-root user,
  `HEALTHCHECK` on `/api/health`) plus a `docker-compose.yml` for local
  operation.

### Changed

- The minimum required library version is
  `libopenschichtplaner5[postgres]>=1.7.0` — the release that introduces the
  calculation facade this version delegates to.

### Fixed

- Overtime endpoints (`/api/employees/{id}/overtime`, `/api/overtime/summary`)
  and the scheduled overtime report delegate to the lib facade — the two
  API-private nominal-hours formulas (`HRSWEEK·MoFr/5`) and the dead
  actual-hours field (constantly 0.0 in the emailed report) are gone.
- `/api/schedule/coverage`, `/api/warnings` (understaffing) and
  `/api/capacity-forecast`/`-year` evaluate real `5SHDEM`/`5SPDEM` demand per
  day index (holiday = 7) with distinct-employee counting and cycle-planned
  duties (5CYASS) included, instead of invented head counts.
- `/api/quality-report` computes staffing via the facade instead of
  nonexistent DBF fields (`HOURS`, `DURATION`, `LEAVETYPEID`).
- Work-time-rules checks (ArbZG extension) run on the spec data basis:
  hours per day index, `5SPSHI` replaces the normal duty instead of being
  added, cycle-planned employees are visible, and rest-time checks use the
  real `STARTEND` windows instead of a guessed 08:00 start.
- Conflict checks (`POST /api/schedule`, conflict report) parse `STARTEND`
  via the lib: up to three windows, holiday index 7, and an empty day slot
  no longer falls back to `STARTEND0`.
- `/api/fairness` counts duty days per the original counters (Sunday and
  holiday columns incl. double counting, new `sunday` field) and no longer
  suppresses planned duties on absence days; `/api/statistics/shifts`
  derives its (API-invented, now documented) Früh/Spät/Nacht heuristic from
  `STARTEND0` instead of the nonexistent `FROM0` field.
- iCal feeds use the holiday time slot `STARTEND7` on holidays.
- `/api/admin/compact` delegates to `SP5Database.compact_database`.
- `/api/health` and `/api/version` report the real installed package version
  (`importlib.metadata`) instead of a hard-coded `1.0.0`.

### Deprecated

- `max_carry_forward_days` on the annual-close endpoints is a documented
  no-op (the spec knows no carry-forward cap; the lib ignores the value).

### Known issues

- `RESTR.WEEKDAY` is still written/read with the legacy convention
  "0 = all days, 1=Mon…7=Sun" by both this API and `sp5lib`'s cycle
  generation, while the original uses the day index 0=Mon…6=Sun, 7=holiday
  (D-34). Fixing it requires a coordinated lib+API change plus a data
  migration for API-written records — tracked as divergence D8.

## [1.1.3] - 2026-06-10

### Fixed

- The `SP5_DB_PATH` default is now published into the environment
  (`os.environ.setdefault` in `sp5api.main`, like `SP5_BACKEND_DIR` already
  was) instead of living only in the module-level `DB_PATH` constant. Without
  it, `sp5lib.auto_migrate` (which reads `SP5_DB_PATH` itself) and the admin
  backup endpoints fell back to their own — different — defaults when the
  variable was unset, so the DBF auto-migration checked a different directory
  than the API served.

## [1.1.2] - 2026-06-10

### Fixed

- `requires-python` corrected to `>=3.12`: the package uses PEP-695 generics
  (`class PaginatedResponse[T]` in `sp5api/schemas.py`), which fail with a
  `SyntaxError` on Python 3.11 — pip happily installed 1.1.1 there and the app
  crashed on import (this is what kept the main app's `python:3.11-slim`
  Docker image from ever starting). The 3.11 classifier is gone too.

## [1.1.1] - 2026-06-10

### Fixed

- **Scheduled exports/reports were broken since their introduction:** four lazy
  `from sp5lib.db import get_db` imports (`export_scheduler`, three report
  generators in `scheduled_reports`) referenced a module that has never existed
  in any libopenschichtplaner5 release — every scheduled Excel/CSV export and
  every scheduled report run failed with `ModuleNotFoundError`. They now use
  the package's own `sp5api.dependencies.get_db` (DBF/PostgreSQL per
  `DB_BACKEND`). The unit tests had masked the bug by injecting a fake
  `sp5lib.db` into `sys.modules`; they now patch the real dependency, and new
  regression tests run the generators against the fixture DB with no import
  mocking.
- Same bug class in `GET /api/admin/cache-stats`: `from sp5lib.cache import
  get_cache_stats` (also nonexistent) made the endpoint permanently serve its
  degraded fallback — it now reports the API's own response-cache statistics
  (`sp5api.cache.stats()`), with the fallback kept.

## [1.1.0] - 2026-06-10

First standalone release. The API was extracted from
[openschichtplaner5](https://github.com/mschabhuettl/openschichtplaner5)'s
`backend/api/` — with full git history (`git filter-repo`) — into its own repo,
mirroring how [libopenschichtplaner5](https://github.com/mschabhuettl/libopenschichtplaner5)
was extracted earlier.

### Added

- Packaging as `openschichtplaner5-api` (setuptools `pyproject.toml`), importable
  as **`sp5api`**. Depends on `libopenschichtplaner5[postgres]>=1.6.0` from PyPI.
- CI workflow (ruff + pytest with coverage gate on Python 3.12/3.13) and release
  workflow publishing to PyPI via Trusted Publishing (OIDC), mirroring the library.
- `sp5api._paths.backend_dir()`: all host-app resource paths (`data/`, `api/data/`,
  `api/uploads/`, state JSON files, frontend dist, changelog) now resolve via the
  `SP5_BACKEND_DIR` environment variable — the same contract `sp5lib` already uses —
  instead of `__file__`-relative paths that break for an installed package.
- `SP5_FRONTEND_DIST` environment variable to point the SPA mount at a built
  frontend explicitly (default: `<SP5_BACKEND_DIR>/../frontend/dist`; if the
  directory is absent the API runs in API-only mode, as before).

### Changed

- Import name `api` → `sp5api` everywhere (package, tests, mock patch targets).
- Runtime-state seeds moved out of the package: `api/data/` and `api/uploads/`
  now live at the repo root (the `SP5_BACKEND_DIR` layout), not inside `sp5api/`,
  so wheels ship only code.
