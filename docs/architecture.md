# Architektur: openschichtplaner5-api (`sp5api`)

> Stand: 2026-06-10 (Paketversion 1.1.3).

---

## 1. Zweck & Architektur

### 1.1 Zweck

`openschichtplaner5-api` ist die **REST-Service-Schicht von OpenSchichtplaner5**: ein
pip-installierbares FastAPI-Paket (Importname **`sp5api`**, analog zu
`libopenschichtplaner5` → `sp5lib`), das Schichtplanungsdaten aus den originalen
Schichtplaner5-FoxPro-`.DBF`-Dateien (oder dem SQLite/PostgreSQL-Mirror der Lib)
als HTTP-API bereitstellt: Auth/2FA mit JWT-Sessions, Mitarbeiter, Dienstplan,
Abwesenheiten, Reports/Exporte, Benachrichtigungen (SSE), Webhooks, iCal-Feeds u.v.m.
Es wurde mit voller Git-Historie aus `backend/api/` der Hauptanwendung extrahiert
(CHANGELOG 1.1.0).

Es gibt **keine CLI** (kein `[project.scripts]` in `pyproject.toml`); der einzige
Einstiegspunkt ist die ASGI-App `sp5api.main:app`
(`python -m uvicorn sp5api.main:app`), plus `python sp5api/main.py` als Dev-Shortcut.

### 1.2 Modulübersicht

| Modul | LOC | Aufgabe |
|---|---:|---|
| `sp5api/main.py` | 1707 | FastAPI-App: Lifespan (Auto-Migration, Auto-Backup, Cleanup-Task, Report-Scheduler), 6 Middlewares, Exception-Handler, Health/Metrics/Version/Stats, 4 Dashboard-Endpoints, SPA-Serving, Router-Registrierung |
| `sp5api/dependencies.py` | 480 | Strukturiertes JSON/Text-Logging (RotatingFileHandler), JWT-Erzeugung/-Prüfung, In-Memory-Session-Store, Brute-Force-Lockout, Rollen-Dependencies (`require_auth/planer/admin/role`), slowapi-Limiter, `get_db()`-Backend-Weiche, Audit-Log (JSON-Lines) |
| `sp5api/_paths.py` | 23 | `backend_dir()`: Ressourcen-Root via `SP5_BACKEND_DIR` (gleicher Vertrag wie `sp5lib`), Fallback = Repo-/Paket-Elternverzeichnis |
| `sp5api/cache.py` | 75 | TTL-In-Memory-Cache (Thread-safe, kein Redis), `get/put/invalidate/clear/stats/get_or_set` |
| `sp5api/rate_limit_store.py` | 116 | 429-Events als JSON-Lines (`data/rate_limit_events.jsonl`), Query + Rotation (max. 10 000) |
| `sp5api/schemas.py` | 129 | Pydantic-Response-Modelle (`extra="allow"`-FlexModels für DBF-Durchreichung), generisches `PaginatedResponse[T]` + `paginate()` |
| `sp5api/types.py` | 20 | Reine Typ-Aliase (`DBFRow`, `EmployeeRecord`, …) |
| `sp5api/routers/` | ~15 800 | 25 Domänen-Router (siehe §2) |

Größte Router: `reports.py` (4306 LOC), `schedule.py` (1657), `misc.py` (1455),
`master_data.py` (978), `employees.py` (975), `absences.py` (861), `admin.py` (841),
`scheduled_reports.py` (840), `auth.py` (729).

### 1.3 Schichten

```
Client (SPA / Kalender-App / Webhook-Empfänger)
   │
   ▼
Middleware-Stack (Ausführungsreihenfolge, außen → innen):
   ChangelogMiddleware (Audit jeder erfolgreichen Schreiboperation)
   → api_versioning_middleware (/api/v1/* → /api/* Rewrite; Deprecation/Sunset-Header auf unversionierten Pfaden)
   → auth_middleware (Token-Pflicht für /api/* außer _PUBLIC_PATHS; 401 vor Routing)
   → request_logging_middleware (X-Request-ID, Timing, Metrics)
   → security_headers_middleware (CSP, XFO, HSTS optional, …)
   → cache_control_middleware (max-age=60 für Stammdaten-GETs)
   → CORS → GZip → SlowAPI (Rate Limiting)
   │
   ▼
Router (sp5api/routers/*) — Rollenprüfung via Depends(require_*)
   │
   ▼
Persistenz (drei parallel genutzte Wege):
   a) sp5lib-Fassade: get_db() → SP5Database(DBF) | SP5PostgresDatabase (DB_BACKEND)
   b) JSON-Dokumentstores unter <SP5_BACKEND_DIR>/data bzw. /api(/data)
   c) sp5lib-ORM (SQLAlchemy) — nur companies-Router + ORM-Mirror
```

**Authentifizierungs-Datenfluss:** `POST /api/auth/login` prüft Benutzer gegen
`5USER.DBF` (`db.verify_user_password`), optional TOTP (`db.totp_verify`), erzeugt
ein signiertes JWT (HS256, `sid`-Claim) **und** registriert die Session serverseitig
in `_sessions` (Revocation-Support). Token-Transport mit Priorität
`X-Auth-Token`-Header → `sp5_token`-HttpOnly-Cookie → `?token=`-Query-Param
(letzteres für SSE/EventSource). Rollenmodell dreistufig: `Leser` < `Planer` < `Admin`.

**Lese-Datenfluss (Beispiel `GET /api/schedule`):** Router → `get_db()` →
`SP5Database.get_schedule()` liest `5MASHI/5SPSHI/5ABSEN` u. a. DBF-Tabellen →
JSON-Antwort; Stammdaten-GETs erhalten `Cache-Control: private, max-age=60`.

**Schreib-Datenfluss:** Router validiert (Pydantic) → `db.add_schedule_entry()` etc.
(DBF-Write via `sp5lib.dbf_writer`) → `cache.invalidate()` → `events.broadcast()`
(SSE an alle Clients) → ggf. `notifications.create_notification()` (+ optional
E-Mail via `sp5lib.email_service`) → ChangelogMiddleware schreibt `db.log_action()`.

**Hintergrund-Jobs (Lifespan):** sp5lib-Auto-Migration (`run_startup_migration`),
DBF-Startup-Check (5 kritische Tabellen), Auto-Backup (max. 1×/24 h, Rotation auf 7),
asyncio-Cleanup-Task (Sessions/Lockouts, Intervall `SESSION_CLEANUP_INTERVAL_MINUTES`),
Thread-Scheduler für **Scheduled Reports** (5-Minuten-Takt).

### 1.4 Konfiguration (wichtigste ENV-Variablen)

`SP5_DB_PATH` (DBF-Verzeichnis; Default wird seit 1.1.3 per `setdefault` ins Environment
publiziert, damit sp5lib denselben Pfad sieht), `SP5_BACKEND_DIR` (Ressourcen-Root),
`SP5_FRONTEND_DIST` (SPA; fehlt das Verzeichnis → reiner API-Modus),
`SP5_JWT_SECRET`/`SECRET_KEY` (sonst Zufallssecret pro Prozess + Warnung),
`SP5_DEV_MODE` (Dev-Token `__dev_mode__` als Admin), `ALLOWED_ORIGINS`,
`DB_BACKEND`/`DATABASE_URL` (PostgreSQL-Mirror), `RATE_LIMIT_API` (100/min),
`RATE_LIMIT_LOGIN` (5/min), `BRUTE_FORCE_MAX_ATTEMPTS`/`_LOCKOUT_MINUTES` (5/15),
`TOKEN_EXPIRE_HOURS` (8), `MAX_SESSIONS_PER_USER` (10), `LOG_FILE`/`LOG_LEVEL`/
`SP5_LOG_FORMAT`, `SP5_AUDIT_LOG`, `SP5_HSTS`, `CSP_REPORT_ONLY`,
`SP5_PW_MIN_LENGTH`/`_REQUIRE_UPPER`/`_REQUIRE_DIGIT`.

---

## 2. Öffentliche Schnittstelle

### 2.1 Python-/Paket-Schnittstelle

- **ASGI-App:** `sp5api.main:app` — der einzige unterstützte Einstiegspunkt.
- `sp5api/__init__.py` ist leer; es gibt keine kuratierte Re-Export-Oberfläche.
  Tests/Hostanwendung greifen direkt auf `sp5api.main` (`_sessions`, `DB_PATH`) und
  `sp5api.dependencies` (`get_db`, `require_*`, `create_jwt_token`, …) zu.
- OpenAPI/Docs: `/api/v1/docs`, `/api/v1/redoc`, `/api/v1/openapi.json`.
- **API-Versionierung:** Jede Route existiert doppelt — kanonisch `/api/...` und
  via Middleware-Rewrite `/api/v1/...` (empfohlen). Unversionierte Aufrufe erhalten
  `Deprecation: true`, `Sunset` (+365 Tage) und `Link: successor-version`.

### 2.2 HTTP-Endpoints (vollständig, 311 Routen; Präfix `/api` ≡ `/api/v1`)

**Legende Rollen:** P=öffentlich (kein Token), L=Leser (jeder eingeloggte User),
PL=Planer+, A=Admin. Quelle in Klammern = Routerdatei.

#### Health / System / Dashboard (`main.py`)

| Methode | Pfad | Rolle | Zweck |
|---|---|---|---|
| GET | `/api` | P | Service-Info |
| GET | `/api/health` | P | Aggregierter Health-Check (DB/Disk/Memory/Uptime/Sessions) |
| GET | `/api/metrics` | P | In-Process-Metriken (Requests, Fehlerrate, Cache-Hitrate, DB-Latenz) |
| GET | `/api/version` | P | API-Version, Python-Version, `min_compatible_frontend` |
| GET | `/api/dev/mode` | P | Ist `SP5_DEV_MODE` aktiv? |
| GET | `/api/migration/status` | L | Schema-Version DBF bzw. Alembic-Revision (PostgreSQL) |
| GET | `/api/stats` | L | DB-Statistik (`db.get_stats()`) |
| GET | `/api/dashboard/summary` | L | Alle Dashboard-KPIs (Besetzung, Abwesenheiten, Zeitkonto-Alerts, Geburtstage, Unterbesetzungswarnungen) |
| GET | `/api/dashboard/today` | L | Heute im Dienst / abwesend, Wochenpeak |
| GET | `/api/dashboard/upcoming` | L | Nächste Feiertage, Geburtstage der Woche |
| GET | `/api/dashboard/stats` | L | Monats-KPIs, Coverage pro Tag, Mitarbeiter-Ranking |
| GET | `/` + `/{full_path:path}` | P | SPA-Serving (index.html-Fallback; unbekannte `/api/*` → 404) |

#### Auth, Users & 2FA (`auth.py`)

| Methode | Pfad | Rolle | Zweck |
|---|---|---|---|
| POST | `/api/auth/login` | P (Rate-Limit `RATE_LIMIT_LOGIN`, Lockout 5 Fehlversuche/15 min pro Username) | Login gegen 5USER.DBF; bei aktiviertem TOTP zweistufig (`requires_2fa: true` ohne `totp_code`); setzt HttpOnly-Cookie `sp5_token` (SameSite=strict, Secure außer Dev) + JWT im Body |
| POST | `/api/auth/logout` | P | Session-Invalidierung (Cookie oder Header), Cookie-Löschung |
| GET | `/api/auth/me` | L | Aktueller Benutzer |
| POST | `/api/auth/change-password` | L (5/min) | Eigenes Passwort ändern (altes Passwort nötig; andere Sessions werden revoked) |
| GET | `/api/users` | A | Benutzerliste |
| POST | `/api/users` | A | Benutzer anlegen (Rollen Admin/Planer/Leser; Passwort-Policy) |
| PUT | `/api/users/{user_id}` | A | Benutzer ändern |
| DELETE | `/api/users/{user_id}` | A | Benutzer soft-deleten (+ Sessions revoken) |
| POST | `/api/users/{user_id}/change-password` | A (5/min) | Passwort setzen (Token-Rotation) |
| POST | `/api/users/{user_id}/reset-password` | PL (5/min) | Temp-Passwort generieren; E-Mail-Versand falls SMTP konfiguriert und Mitarbeiter-E-Mail auffindbar |
| GET | `/api/auth/2fa/status` | L | TOTP aktiviert? |
| POST | `/api/auth/2fa/setup` | L | TOTP-Secret + QR-Code (base64-PNG, `pyotp`/`qrcode`) |
| POST | `/api/auth/2fa/enable` | L (10/min) | Code verifizieren, 2FA aktivieren, **Backup-Codes** zurückgeben |
| POST | `/api/auth/2fa/disable` | L (5/min) | 2FA deaktivieren (Passwort-Bestätigung) |
| POST | `/api/auth/2fa/admin-disable/{user_id}` | A | 2FA fremddeaktivieren (Geräteverlust) |

Alle TOTP-Operationen delegieren an die Lib (`db.totp_generate_secret/enable/verify/
disable/get_status`). Alle sicherheitsrelevanten Aktionen schreiben ins Audit-Log
(`write_audit_log`, JSON-Lines, `SP5_AUDIT_LOG`).

#### Companies / Multi-Tenant (`companies.py` — ORM-basiert)

| Methode | Pfad | Rolle |
|---|---|---|
| GET | `/api/companies` | A |
| GET | `/api/companies/{company_id}` | A |
| POST | `/api/companies` | A (nur „Super-Admin“ = Admin ohne `company_id`) |
| PUT | `/api/companies/{company_id}` | A |
| DELETE | `/api/companies/{company_id}` | A (Soft-Delete `is_active=False`; 409 falls Mitarbeiter/Gruppen zugeordnet) |

#### Mitarbeiter & Gruppen (`employees.py`, `qualification_matrix.py`, `availability.py`)

| Methode | Pfad | Rolle |
|---|---|---|
| GET | `/api/employees` | L (Filter `include_hidden`, `group_id`, Pagination) |
| GET | `/api/employees/{emp_id}` | L |
| POST | `/api/employees` | A |
| PUT | `/api/employees/{emp_id}` | A |
| DELETE | `/api/employees/{emp_id}` | A (Soft-Delete = verstecken) |
| PUT | `/api/employees/{emp_id}/activate` | A (Reaktivieren) |
| GET | `/api/employees/{emp_id}/photo` | L (WebP aus `api/uploads/photos/`) |
| POST | `/api/employees/{emp_id}/photo` | A (Upload, Pillow-Konvertierung → WebP) |
| POST | `/api/employees/import-csv` | A (CSV-Import) |
| POST | `/api/employees/bulk` | A (Bulk-Aktionen hide/show/group-assign) |
| GET | `/api/groups` | L |
| GET | `/api/groups/{group_id}/members` | L |
| POST | `/api/groups` | A |
| PUT | `/api/groups/{group_id}` | A |
| DELETE | `/api/groups/{group_id}` | A |
| POST | `/api/groups/{group_id}/members` | A |
| DELETE | `/api/groups/{group_id}/members/{emp_id}` | A |
| GET | `/api/group-assignments` | L (alle employee↔group-Paare; in `absences.py`) |
| GET | `/api/employees/qualification-matrix` | PL (Quali-Matrix aus `NOTE1`-Feld geparst) |
| GET | `/api/qualifications/stats` | PL |
| GET | `/api/employees/{emp_id}/availability` | PL (JSON-Store `api/data/availability.json`) |
| POST | `/api/employees/{emp_id}/availability` | PL |
| PUT | `/api/employees/{emp_id}/availability` | PL |

#### Dienstplan (`schedule.py`, `schedule_comments.py`, `schedule_pdf.py`)

| Methode | Pfad | Rolle |
|---|---|---|
| GET | `/api/schedule` | L (Monatsplan; Gruppe optional) |
| GET | `/api/schedule/day` · `/week` · `/year` | L |
| GET | `/api/schedule/coverage` | L (Besetzungsanalyse) |
| GET | `/api/schedule/conflicts` | L (Konflikterkennung) |
| POST | `/api/schedule` | PL (Eintrag anlegen: Schicht/Abwesenheit; Konflikt-/Restriktions-Checks) |
| DELETE | `/api/schedule/{employee_id}/{date}` | PL |
| DELETE | `/api/schedule-shift/{employee_id}/{date}` | PL (nur Schicht-Override) |
| POST | `/api/schedule/bulk` | PL (Bulk-Operationen) |
| POST | `/api/schedule/bulk-group` | PL (Schicht ganzer Gruppe zuweisen) |
| POST | `/api/schedule/generate` | PL (Auto-Generierung aus Zyklen) |
| POST | `/api/schedule/swap` | PL (Tausch zwischen 2 Mitarbeitern, MASHI/SPSHI/ABSEN) |
| POST | `/api/schedule/copy-week` | PL |
| GET/POST | `/api/schedule/templates`, POST `/capture`, DELETE `/{id}`, POST `/{id}/apply` | PL (Wochen-Templates) |
| GET | `/api/cycles` | L |
| GET/POST | `/api/shift-cycles`, GET/PUT/DELETE `/{cycle_id}` | L bzw. PL |
| GET/POST | `/api/shift-cycles/assign`, DELETE `/assign/{employee_id}` | PL |
| GET/POST | `/api/cycle-exceptions`, DELETE `/{exception_id}` | PL |
| GET | `/api/staffing` | L (Soll/Ist pro Monat) |
| GET/POST | `/api/restrictions`, DELETE `/{employee_id}/{shift_id}` | L/A (Schichtsperren, 5RESTR) |
| POST/GET | `/api/einsatzplan`, PUT/DELETE `/{entry_id}`, POST `/deviation` | PL (Einsatzplan/Abweichungen) |
| GET/POST | `/api/schedule/comments`, DELETE `/{comment_id}` | L/PL (Tageskommentare, via `db.get_schedule_comments`) |
| GET | `/api/schedule/pdf` | PL (**HTML-Druckansicht** A4 quer, kein echtes PDF) |

#### Abwesenheiten & Urlaub (`absences.py`)

| Methode | Pfad | Rolle |
|---|---|---|
| GET | `/api/absences` | L (Filter Jahr/Monat/Mitarbeiter/Typ, Pagination) |
| POST | `/api/absences` | PL (mit Urlaubssperren-/Saldo-Prüfung) |
| POST | `/api/absences/bulk` | PL (mehrere Mitarbeiter) |
| DELETE | `/api/absences/{employee_id}/{date}` | PL |
| GET | `/api/absences/status` | PL (Genehmigungsstatus, JSON-Store `api/absence_status.json`) |
| PATCH | `/api/absences/{absence_id}/status` | PL (approve/reject + Notification/E-Mail) |
| GET | `/api/absences/stats/employee/{employee_id}` | PL |
| GET | `/api/absences/stats/group/{group_id}` | PL |
| GET | `/api/absences/stats/overview` | PL |
| GET/POST | `/api/leave-entitlements` | L/PL (Urlaubsanspruch, 5LEAEN) |
| GET | `/api/leave-balance`, `/api/leave-balance/group` | L (Resturlaub) |
| GET/POST | `/api/holiday-bans`, DELETE `/{ban_id}` | L/A (Urlaubssperren, 5HOBAN) |
| GET | `/api/annual-close/preview` | A (Jahresabschluss-Vorschau) |
| POST | `/api/annual-close` | A (Salden-Übertrag ins Folgejahr) |

#### Stammdaten (`master_data.py`)

| Methode | Pfad | Rolle |
|---|---|---|
| GET/POST | `/api/shifts`, PUT/DELETE `/{shift_id}` | L/A (Löschen: Soft-Hide bei aktiver Nutzung) |
| GET/POST | `/api/leave-types`, PUT/DELETE `/{lt_id}` | L/A |
| GET/POST | `/api/holidays`, PUT/DELETE `/{holiday_id}` | L/A |
| GET/POST | `/api/workplaces`, PUT/DELETE `/{wp_id}` | L/A |
| GET | `/api/workplaces/{wp_id}/employees` | L |
| POST/DELETE | `/api/workplaces/{wp_id}/employees/{employee_id}` | A |
| GET/POST | `/api/extracharges`, PUT/DELETE `/{xc_id}`, GET `/summary` | L/A (Zuschläge, 5XCHAR) |
| GET/POST | `/api/staffing-requirements` | L/PL (Personalbedarf, 5SHDEM) |
| GET/POST | `/api/staffing-requirements/special`, PUT/DELETE `/{record_id}` | L/PL (5SPDEM) |
| GET/POST | `/api/skills`, PUT/DELETE `/{skill_id}` | L/A (JSON-Store `data/skills.json`) |
| GET/POST | `/api/skills/assignments`, DELETE `/{assignment_id}` | L/A |
| GET | `/api/skills/matrix` | L (Mitarbeiter × Skills) |

#### Reports, Statistiken, Export/Import (`reports.py`, `conflict_report.py`, `overtime.py`)

| Methode | Pfad | Rolle | Format |
|---|---|---|---|
| GET | `/api/statistics` | L | Monatsstatistik (Soll/Ist/Überstunden je Mitarbeiter) |
| GET | `/api/statistics/year-summary` | L | |
| GET | `/api/statistics/employee/{emp_id}` | L | |
| GET | `/api/statistics/sickness` | L | Krankenstand |
| GET | `/api/statistics/shifts` | L | Schicht-Trend |
| GET | `/api/export/schedule` | L | **csv / html / xlsx** (openpyxl, farbige Zellen) |
| GET | `/api/export/statistics` | L | csv / html / xlsx |
| GET | `/api/export/employees` | L | csv |
| GET | `/api/export/absences` | L | csv |
| GET | `/api/reports/monthly` | L | Monatsabschluss **csv / pdf** (echtes PDF via fpdf2, A4 quer, Summenzeile) |
| GET | `/api/zeitkonto` · `/detail` · `/summary` | L | Zeitkonto (Saldo, Verlauf, Teamsummen) |
| GET/POST | `/api/bookings`, DELETE `/{booking_id}` | L/PL | Manuelle Stundenbuchungen (5BOOK) |
| GET/POST | `/api/bookings/carry-forward` | L/PL | Übertragssaldo |
| POST | `/api/bookings/annual-statement` | PL | Jahresabrechnung generieren |
| GET | `/api/overtime-records` | L | 5OVER-Einträge |
| GET | `/api/employees/{emp_id}/overtime` | PL | Über-/Unterstunden je Monat |
| GET | `/api/overtime/summary` | PL | Team-Überstunden |
| GET | `/api/overtime-summary` | L | (zweite, ältere Variante in `reports.py`) |
| POST | `/api/import/employees` · `/shifts` · `/absences` · `/holidays` · `/bookings-actual` · `/bookings-nominal` · `/entitlements` · `/absences-csv` · `/groups` | A | CSV-Importe (max. 10 MB, Content-Type-Whitelist) |
| GET | `/api/burnout-radar` | L | Belastungsanalyse |
| GET | `/api/warnings` | L | Plan-Anomalien |
| GET | `/api/fairness` | L | Fairness-Verteilung (Wochenenden/Nächte/…) |
| GET | `/api/capacity-forecast`, `/api/capacity-year` | L | Kapazitätsprognose |
| GET | `/api/quality-report` | L | Planqualität |
| GET | `/api/availability-matrix` | L | |
| POST | `/api/simulation` | L | Was-wäre-wenn-Simulation |
| GET | `/api/reports/conflicts` | PL | Konfliktreport (overlap / double_booked / understaffed) |
| GET | `/api/reports/conflicts/export` | PL | csv / xlsx |

#### Selfservice, Notizen, Wünsche, Tausch (`misc.py`)

| Methode | Pfad | Rolle |
|---|---|---|
| GET/POST | `/api/notes`, PUT/DELETE `/{note_id}` | L/PL (Schichtnotizen, 5NOTE) |
| GET | `/api/search` | L (Volltext über Mitarbeiter/Schichten/Gruppen/…) |
| GET/POST | `/api/employee-access`, DELETE `/{access_id}` | A (Zugriffsrechte 5EMACC) |
| GET/POST | `/api/group-access`, DELETE `/{access_id}` | A (5GRACC) |
| GET/POST | `/api/changelog` | L/PL (Audit-Trail, via `db.log_action`-Store) |
| GET/POST | `/api/wishes`, DELETE `/{wish_id}`, PATCH `/{wish_id}/approve` | L/PL (Schichtwünsche/Sperrtage) |
| GET/POST | `/api/handover`, PATCH/DELETE `/{note_id}` | L/PL (Übergabebuch) |
| GET/POST | `/api/swap-requests`, PATCH `/{swap_id}/resolve`, DELETE `/{swap_id}` | L/PL (Tauschbörse; bei Annahme echter Plan-Tausch + Notifications/E-Mail) |
| GET | `/api/shifts/swap/{swap_id}/history` | L |
| POST | `/api/shifts/swap/expire` | PL (alte Anfragen ablaufen lassen) |
| POST | `/api/self/swap-requests` | L (eigener Tausch-Antrag) |
| PATCH | `/api/self/swap-requests/{swap_id}/respond` | L (Partner nimmt an/lehnt ab) |
| DELETE | `/api/self/swap-requests/{swap_id}` | L |
| GET | `/api/me/employee` | L (eigener Mitarbeiterdatensatz) |
| GET/POST | `/api/self/wishes`, DELETE `/{wish_id}` | L |
| GET | `/api/self/schedule` | L |
| POST | `/api/self/absences` | L (eigener Abwesenheitsantrag → Genehmigungsworkflow) |
| GET | `/api/release-notes` | L (liefert CHANGELOG.md) |

#### Admin, Backup, Settings (`admin.py`)

| Methode | Pfad | Rolle |
|---|---|---|
| GET/POST | `/api/periods`, DELETE `/{period_id}` | L/A (Abrechnungsperioden, 5PERIO) |
| GET/PUT | `/api/settings` | L/A (5USETT) |
| GET | `/api/admin/backups` | A (ZIP-Backups in `<DB_PATH>/../backups`, Rotation 7) |
| GET | `/api/admin/backups/{filename}/download` | A |
| DELETE | `/api/admin/backups/{filename}` | A |
| GET | `/api/backup/download` | A (Live-ZIP der DBF/FPT/CDX-Dateien) |
| GET | `/api/backup/sqlite` | A (Export als SQLite via `sp5lib.sqlite_adapter`) |
| POST | `/api/backup/restore` | A (ZIP-Upload, Validierung Pflichtdateien) |
| POST | `/api/admin/compact` | A (DBF PACK) |
| POST | `/api/errors` | P (10/min; Frontend-Error-Reports → `data/frontend_errors.json`, max. 500) |
| GET | `/api/admin/frontend-errors` | A |
| GET | `/api/admin/rate-limits` | A (429-Event-Dashboard aus `rate_limit_store`) |
| GET | `/api/admin/cache-stats` | A (`sp5api.cache.stats()`) |

#### Events, Notifications, iCal, E-Mail (`events.py`, `notifications.py`, `notification_settings.py`, `ical.py`, `email.py`)

| Methode | Pfad | Rolle |
|---|---|---|
| GET | `/api/events` | L (**SSE-Stream**; Token auch als `?token=`; Keepalive alle 25 s; In-Memory-Subscriber-Registry) |
| GET | `/api/notifications` | L (eigene ungelesene; JSON-Store `api/notifications.json`) |
| GET | `/api/notifications/all` | A |
| PATCH | `/api/notifications/{notif_id}/read`, `/api/notifications/read-all` | L |
| DELETE | `/api/notifications/{notif_id}` | L |
| GET/PUT | `/api/notifications/settings` | L (E-Mail-Trigger je User, `data/notification_settings.json`) |
| GET | `/api/ical/my-schedule.ics` | L (Monats-Download) |
| GET | `/api/ical/schedule/{employee_id}.ics` | PL |
| GET | `/api/ical/feed/{token}.ics` | P (**Token-in-URL**; rollierendes Fenster −1/+3 Monate; Token via `db.create_ical_token`) |
| POST/GET/DELETE | `/api/ical/token` | L (Feed-Token erzeugen/abfragen/widerrufen; liefert auch `webcal://`-URL) |
| GET | `/api/email/config` | A (SMTP-Status aus `sp5lib.email_service`) |
| POST | `/api/email/test` | A |

#### Webhooks (`webhooks.py`)

| Methode | Pfad | Rolle |
|---|---|---|
| GET | `/api/webhooks`, `/api/webhooks/{webhook_id}` | A |
| POST | `/api/webhooks` | A (Secret wird generiert) |
| PUT/DELETE | `/api/webhooks/{webhook_id}` | A |
| POST | `/api/webhooks/{webhook_id}/test` | A |
| GET | `/api/webhooks/events/list` | A |

Events: `shift.created/updated/deleted`, `absence.created/approved`. Auslieferung mit
HMAC-SHA256-Signatur (`X-SP5-Signature`), 3 Retries mit Backoff, `last_delivery`-Status
im JSON-Store `data/webhooks.json`.

#### Wiederkehrende Schichten, Export-/Report-Scheduler, Arbeitszeitregeln

| Methode | Pfad | Rolle |
|---|---|---|
| GET/POST | `/api/shifts/recurring`, DELETE `/{pattern_id}`, POST `/{pattern_id}/generate` | PL (wöchentl./14-tägl. Muster, JSON-Store `api/data/recurring_shifts.json`) |
| GET/POST | `/api/export-scheduler/schedules`, PUT/DELETE `/{schedule_id}`, POST `/{schedule_id}/run` | PL/A (wöchentl. Excel/CSV-Export per E-Mail; `data/export_schedules.json`) |
| GET/POST | `/api/scheduled-reports`, GET/PUT/DELETE `/{report_id}`, POST `/{report_id}/run`, GET `/scheduler/status` | PL/A (Reporttypen `schedule_overview`/`overtime`/`absences`; daily/weekly/monthly; xlsx/csv; Thread-Scheduler alle 5 min; `data/scheduled_reports.json`) |
| GET/PUT | `/api/work-time-rules` | L/A (Regelkonfiguration, `data/work_time_rules.json`) |
| POST | `/api/work-time-rules/check`, `/check-all` | L (Verstoß-Prüfung: Ruhezeiten, max. Folgetage usw.) |

#### ORM-Mirror (`orm_mirror.py`, Präfix `/api/admin/orm`, alle Admin-only)

`POST /sync` (materialisiert alle 19 DBF-Tabellen via `sp5lib.orm.sync.sync_all` in
eine SQLite-Projektion `sp5_orm.db` neben dem DBF-Verzeichnis), `GET /status`,
sowie Read-only-Listen: `/shifts`, `/leave-types`, `/workplaces`,
`/shift-assignments`, `/special-shifts`, `/absences` (mit Datumsbereich),
`/holidays`, `/periods`, `/bookings`, `/overtime`, `/leave-entitlements`,
`/shift-demands`, `/special-demands`, `/cycles`, `/cycle-assignments`,
`/restrictions` — der schrittweise DBF→ORM-Migrationspfad der Lib.

---

## 3. Feature-Inventur (IST-Stand)

**Vollständig implementiert und getestet** (2574 Tests, Coverage-Gate 70 % in CI):

- **Auth-Stack:** JWT (HS256) + serverseitiger Session-Store mit Revocation,
  HttpOnly-Cookie + Header + Query-Token, Rollenhierarchie Leser/Planer/Admin,
  Brute-Force-Lockout, Session-Limit pro User (Eviction), Passwort-Policy (ENV),
  TOTP-2FA inkl. QR-Setup, Backup-Codes und Admin-Reset, Audit-Log (JSON-Lines),
  Dev-Mode-Token.
- **Komplette CRUD-Abdeckung** der SP5-Domäne: Mitarbeiter (inkl. Foto-Upload/WebP,
  Soft-Delete), Gruppen + Mitgliedschaften, Schichten, Abwesenheits-/Urlaubsarten,
  Feiertage, Arbeitsplätze, Zuschläge, Personalbedarf (regulär + speziell),
  Perioden, Settings, Benutzer, Zugriffsrechte (Employee-/Group-Access).
- **Dienstplan:** Monats-/Wochen-/Tages-/Jahresansichten, Schreiben mit Konflikt-
  und Restriktionsprüfung, Bulk/Bulk-Group, Wochen-Templates (capture/apply),
  Copy-Week, Schichttausch, Schichtzyklen inkl. Zuweisungen/Ausnahmen und
  Auto-Generierung, Einsatzplan mit Abweichungserfassung, Tageskommentare.
- **Abwesenheits-Workflow:** Anträge (auch Selfservice), Genehmigungsstatus
  (approve/reject mit Benachrichtigung), Urlaubskonto/Resturlaub, Urlaubssperren,
  Jahresabschluss mit Vorschau.
- **Reporting/Analytics:** Monats-/Jahresstatistik, Zeitkonto (3 Endpoints),
  Krankenstand, Burnout-Radar, Fairness, Kapazitätsprognose (Monat + Jahr),
  Qualitätsreport, Warnungen, Verfügbarkeitsmatrix, Simulation, Konfliktreport.
- **Exporte:** CSV/HTML/XLSX (Dienstplan, Statistik, Mitarbeiter, Abwesenheiten,
  Konflikte), echtes PDF nur für den Monatsabschluss (fpdf2); 9 CSV-Importe;
  Backup als ZIP und SQLite; HTML-Druckansicht des Plans.
- **Integrationen:** SSE-Echtzeit-Events, In-App-Notifications (+ optionale E-Mails
  über `sp5lib.email_service` mit Per-User-Settings), iCal-Download und
  abonnierbarer Token-Feed (webcal), signierte Webhooks mit Retry,
  Scheduled Reports (Thread-Scheduler) und Export-Schedules (manueller Trigger).
- **Betrieb/Observability:** Health-Aggregat (DB/Disk/Memory), In-Process-Metriken,
  strukturiertes JSON-Logging mit Request-ID-Propagierung, Rate-Limiting mit
  429-Dashboard, Security-Header inkl. CSP, Cache-Control für Stammdaten,
  Frontend-Error-Inbox, Auto-Backup + Rotation, DBF-Auto-Migration beim Start,
  API-Versionierung mit Deprecation-Headern, SPA-Hosting.
- **Multi-Tenant (experimentell):** Company-CRUD auf ORM-Basis + Migrationsskript
  `scripts/migrate_add_company.py` (idempotent: Tabelle, `company_id`-Spalten,
  Default-Company, Backfill).
- **Backends:** DBF (Default) und PostgreSQL-Mirror über `DB_BACKEND`/`DATABASE_URL`
  (Weiche in `dependencies.get_db()`); ORM-Mirror-Endpoints für die schrittweise
  Migration.

---

## 4. Cross-Repo-Verdrahtung

```
openschichtplaner5 (App)          openschichtplaner5-api (dieses Repo)     libopenschichtplaner5 (Lib)
  backend/requirements.txt          pyproject.toml                            PyPI: libopenschichtplaner5
  ├── openschichtplaner5-api>=1.1.1 ──► sp5api                                ▲
  └── libopenschichtplaner5[postgres]>=1.6.0   └── libopenschichtplaner5[postgres]>=1.6.0 ──┘ (Import: sp5lib)
```

**Konsumiert (downstream → upstream):**

- Dieses Repo hängt in `pyproject.toml` von **`libopenschichtplaner5[postgres]>=1.6.0`**
  (PyPI-Release) ab, importiert als `sp5lib`. Genutzte Lib-Oberfläche:
  - `sp5lib.database.SP5Database` — die zentrale Fassade (~60 verwendete Methoden:
    `get_employees/get_schedule*/add_absence/verify_user_password/totp_*/
    create_ical_token/get_swap_requests/log_action/...`), instanziiert in
    `sp5api/dependencies.py:get_db()`.
  - `sp5lib.db_config` / `sp5lib.db_factory` — Backend-Weiche DBF↔PostgreSQL.
  - `sp5lib.auto_migrate` — Startup-Migration + `/api/migration/status`.
  - `sp5lib.dbf_reader.get_table_fields` / `sp5lib.dbf_writer.find_all_records` —
    Low-Level-Zugriffe in `schedule.py`, `recurring_shifts.py`, `misc.py`.
  - `sp5lib.orm.*` (Engine/Session/Modelle/Repositories/`sync_all`) —
    `companies.py`, `orm_mirror.py`, `scripts/migrate_add_company.py`.
  - `sp5lib.email_service` — SMTP-Versand (`auth`, `email`, `misc`,
    `notifications`, `export_scheduler`, `scheduled_reports`).
  - `sp5lib.sqlite_adapter` (`admin.py`), `sp5lib.color_utils.bgr_to_hex` (`main.py`).

- **Geteilter Pfad-Vertrag:** `SP5_BACKEND_DIR` (von `sp5api._paths.backend_dir()`
  beim Import von `main.py` per `setdefault` publiziert, **bevor** sp5lib importiert
  wird) ist der gemeinsame Ressourcen-Root beider Pakete (`data/`, `api/data/`,
  Alembic-Konfiguration). Seit 1.1.3 wird auch der `SP5_DB_PATH`-Default ins
  Environment publiziert, damit `sp5lib.auto_migrate` denselben DBF-Pfad sieht.

**Konsumiert von (upstream → downstream):**

- Die Hauptanwendung `openschichtplaner5` pinnt in `backend/requirements.txt`
  **`openschichtplaner5-api>=1.1.1`** und zusätzlich direkt
  `libopenschichtplaner5[postgres]>=1.6.0`; ihr `Dockerfile`/`start.sh` setzen
  `SP5_BACKEND_DIR` und starten `uvicorn sp5api.main:app`. (Hinweis: das
  Meta-Workspace-Dokument spricht von „git deps (@main)“ — tatsächlich verdrahtet
  ist aktuell der **PyPI-Versions-Pin**.)

**Release-Workflow (PyPI):** Tag `vX.Y.Z` pushen → `.github/workflows/release.yml`
baut sdist+wheel (`python -m build`, `twine check --strict`) und publiziert via
**Trusted Publishing (OIDC)** — keine gespeicherten Tokens. CI
(`.github/workflows/ci.yml`): ruff + pytest mit `--cov-fail-under=70` auf
Python 3.12/3.13.

**Editable-`../`-Workflow (lokal):** Die drei Repos liegen als Siblings;
Entwicklung gegen den lokalen Lib-Stand via
`pip install -e . -e "../libopenschichtplaner5[postgres]"` (README §Development).
In diesem Checkout dient der Repo-Root selbst als `SP5_BACKEND_DIR`
(`data/`, `api/data/` sind eingecheckte Runtime-State-Seeds für die Tests;
`tests/fixtures/` enthält eine komplette DBF-Fixture-Datenbank mit 26 Tabellen).

---

## 5. Lücken, Altlasten, Auffälligkeiten

**Funktionale Lücken:**

1. **Export-Scheduler hat keinen Scheduler.** `export_scheduler.py` validiert
   `frequency="weekly"`, `day_of_week` und `time`, aber es existiert kein
   Hintergrund-Loop, der fällige Exporte ausführt — nur der manuelle Trigger
   `POST /api/export-scheduler/schedules/{id}/run`. Der Lifespan in
   `sp5api/main.py` startet ausschließlich den Scheduler aus
   `scheduled_reports.py`. Die konfigurierten Zeitpunkte sind also wirkungslos
   (Feature-Duplikat: `scheduled_reports` kann fast dasselbe, mit echtem Scheduler).
2. **`GET /api/schedule/pdf` liefert kein PDF**, sondern eine HTML-Druckansicht
   („Ctrl+P → Save as PDF“, `sp5api/routers/schedule_pdf.py`). Name/Tag „Export“
   sind irreführend; echtes PDF gibt es nur bei `/api/reports/monthly?format=pdf`.
3. **Multi-Tenant ist nur halb verdrahtet.** `companies.py` filtert nach
   `user.get("company_id")`, aber weder `login` noch `dependencies.py` setzen je
   ein `company_id` in die Session (das 5USER-DBF-Schema kennt das Feld nicht) —
   praktisch ist jeder Admin „Super-Admin“. Zudem nutzt `companies.py`
   `sqlite:///<dirname(DB_PATH)>/sp5_orm.db`, während
   `scripts/migrate_add_company.py` als Default `sqlite:///sp5.db` (CWD-relativ)
   verwendet — zwei verschiedene Datenbankpfade für dasselbe Feature.

**Konsistenz/Architektur:**

4. **Dreifacher Versions-Drift:** `pyproject.toml` = 1.1.3,
   `FastAPI(version="1.1.0")` (`main.py:307`), `_API_VERSION = "1.0.0"`
   (`main.py:850`, von `/api/version` und `/api/health` ausgeliefert). Keine der
   drei Stellen wird aus den anderen abgeleitet.
5. **Lib-Fassade wird umgangen:** 37× `db._read(...)` und 8× `db._table(...)`
   (private Methoden der `SP5Database`) plus direkte
   `sp5lib.dbf_writer.find_all_records`-Zugriffe — konzentriert in
   `reports.py` (17), `schedule.py` (9), `work_time_rules.py` (7), `main.py` (6).
   Das koppelt die API eng an Lib-Interna und unterläuft die PostgreSQL-Abstraktion
   (Raw-DBF-Lesen funktioniert im PG-Backend nur, soweit die Fassade es emuliert).
6. **Zweierlei Persistenz-Welten:** Neben den DBF-Tabellen existieren ~14
   JSON-Dokumentstores (`webhooks.json`, `availability.json`,
   `recurring_shifts.json`, `work_time_rules.json`, `export_schedules.json`,
   `scheduled_reports.json`, `notifications.json`, `absence_status.json`,
   `frontend_errors.json`, `skills.json`, `notification_settings.json`,
   `rate_limit_events.jsonl`, …) mit uneinheitlichem Layout
   (`data/` vs. `api/` vs. `api/data/`) und nur prozesslokalem Locking.
7. **Single-Process-Annahmen:** Session-Store, SSE-Subscriber-Registry, Metriken,
   TTL-Cache und Failed-Login-Tracking sind In-Memory-Dicts — explizit dokumentiert
   (`dependencies.py:209`), aber damit sind Multi-Worker-Deployments
   (uvicorn `--workers > 1`) funktional ausgeschlossen (Sessions/SSE brechen).
8. **`routers/__init__.py` listet nur 9 der 25 Router**; `main.py` importiert alle
   25 direkt. Harmlos, aber das `__all__` suggeriert eine falsche Paket-Oberfläche.
9. **Doppelte/überlappende Endpoints:** `/api/overtime-summary` (reports.py) vs.
   `/api/overtime/summary` (overtime.py); CSV-Import doppelt als
   `/api/employees/import-csv` und `/api/import/employees`;
   `/api/import/absences` und `/api/import/absences-csv` (Alternativformat).

**Sicherheits-Auffälligkeiten (klein, aber konkret):**

10. **`/api/metrics` ist öffentlich für alle**, obwohl Summary/Docstring
    („No authentication required **when called from localhost**“, `main.py:1020 ff.`)
    eine Localhost-Beschränkung suggerieren — `is_local` wird nur berechnet und im
    Response-Feld `local_request` zurückgegeben, nicht durchgesetzt. Exponiert
    u. a. `active_sessions` und Fehlerraten anonym.
11. `_generate_temp_password` (`auth.py:339`) nutzt `random` statt `secrets` —
    für ein per E-Mail verschicktes Temp-Passwort wäre CSPRNG angemessen.

**Altlasten (aus der Monorepo-Extraktion):**

12. `api/uploads/photos/40.webp`, `41.webp` sind eingecheckte Foto-Uploads
    (Runtime-State als Fixture-Seed); ebenso liegen zwei komplette Backup-ZIPs
    unter `tests/backups/`. Funktional ok, aber nirgends als Fixtures dokumentiert
    (README erwähnt nur `data/`-Seeds).
13. `openschichtplaner5_api.egg-info/` liegt im Arbeitsverzeichnis (untracked,
    `.gitignore` greift) — reines lokales Build-Artefakt.

**Kleinigkeiten:** `_TZ_VIENNA` in `ical.py` ist fix UTC+1 (keine CEST-Umschaltung;
der Kommentar daneben behauptet „we use UTC and let clients handle TZ“, tatsächlich
werden Events mit +01:00 erzeugt — im Sommer eine Stunde versetzt);
`GET /api/events` trägt das Tag „Events“ doppelt; das OpenAPI-Tag
`work-time-rules` bricht die sonst durchgehende Title-Case-Konvention der Tags.

---

### Anhang: Verifikationskommandos

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]" -e "../libopenschichtplaner5[postgres]"
ruff check .          # sauber
pytest                # 2574 passed, 6 skipped (~63 s)
SP5_DB_PATH=tests/fixtures python -m uvicorn sp5api.main:app  # API-only-Modus
```
