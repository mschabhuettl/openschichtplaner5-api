"""Admin router: users, settings, backup, periods, admin tasks."""
import os
import io
import zipfile
import json
from datetime import datetime as _backup_dt
from fastapi import APIRouter, HTTPException, Query, Depends, Request, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional
from ..dependencies import (
    get_db, require_admin, require_planer, require_auth, _sanitize_500, _logger, limiter,
)

router = APIRouter()



# ── Periods ───────────────────────────────────────────────────

@router.get("/api/periods", tags=["Admin"], summary="List accounting periods")
def get_periods(
    group_id: Optional[int] = Query(None),
    _cur_user: dict = Depends(require_auth),
):
    return get_db().get_periods(group_id=group_id)


class PeriodCreate(BaseModel):
    group_id: int
    start: str  # YYYY-MM-DD
    end: str    # YYYY-MM-DD
    description: str = ''


@router.post("/api/periods", tags=["Admin"], summary="Create accounting period")
def create_period(body: PeriodCreate, _cur_user: dict = Depends(require_planer)):
    try:
        from datetime import datetime
        datetime.strptime(body.start, '%Y-%m-%d')
        datetime.strptime(body.end, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Ungültiges Datumsformat, erwartet YYYY-MM-DD")
    if body.end < body.start:
        raise HTTPException(status_code=400, detail="end muss >= start sein")
    try:
        result = get_db().create_period({
            'group_id': body.group_id,
            'start': body.start,
            'end': body.end,
            'description': body.description,
        })
        return {"ok": True, "record": result}
    except Exception as e:
        raise _sanitize_500(e)


@router.delete("/api/periods/{period_id}", tags=["Admin"], summary="Delete accounting period")
def delete_period(period_id: int, _cur_user: dict = Depends(require_planer)):
    try:
        count = get_db().delete_period(period_id)
        return {"ok": True, "deleted": count}
    except Exception as e:
        raise _sanitize_500(e)


# ── Settings (USETT) ─────────────────────────────────────────

@router.get("/api/settings", tags=["Admin"], summary="Get application settings")
def get_settings(_cur_user: dict = Depends(require_auth)):
    """Return global settings from 5USETT.DBF."""
    try:
        return get_db().get_usett()
    except Exception as e:
        raise _sanitize_500(e)


class SettingsUpdate(BaseModel):
    ANOANAME: Optional[str] = None
    ANOASHORT: Optional[str] = None
    ANOACRTXT: Optional[int] = None
    ANOACRBAR: Optional[int] = None
    ANOACRBK: Optional[int] = None
    ANOABOLD: Optional[int] = None
    BACKUPFR: Optional[int] = None


@router.put("/api/settings", tags=["Admin"], summary="Update application settings")
def update_settings(body: SettingsUpdate, _cur_user: dict = Depends(require_admin)):
    """Update global settings in 5USETT.DBF."""
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        result = get_db().update_usett(data)
        _logger.warning(
            "AUDIT SETTINGS_UPDATE | user=%s fields=%s",
            _cur_user.get('NAME'), list(data.keys())
        )
        return {"ok": True, "record": result}
    except Exception as e:
        raise _sanitize_500(e)


# ── Backup / Restore endpoints ───────────────────────────────


_BACKUP_ALLOWED_EXT = {'.DBF', '.FPT', '.CDX'}
_BACKUP_MAX_COUNT = 7


def _get_db_path() -> str:
    return os.environ.get('SP5_DB_PATH', '')


def _get_backup_dir() -> str:
    db_path = _get_db_path()
    if not db_path:
        return ''
    backup_dir = os.path.join(os.path.dirname(db_path), 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    return backup_dir


def _create_zip_bytes(db_path: str) -> bytes:
    """Create a ZIP of all DBF/FPT/CDX files and return as bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
        for fname in sorted(os.listdir(db_path)):
            ext = os.path.splitext(fname)[1].upper()
            if ext in _BACKUP_ALLOWED_EXT:
                full_path = os.path.join(db_path, fname)
                if os.path.isfile(full_path):
                    zf.write(full_path, arcname=fname)
    return buf.getvalue()


def _rotate_backups(backup_dir: str, max_count: int = _BACKUP_MAX_COUNT):
    """Keep only the newest max_count backup files."""
    files = sorted(
        [f for f in os.listdir(backup_dir) if f.startswith('sp5_backup_') and f.endswith('.zip')],
        reverse=True
    )
    for old in files[max_count:]:
        try:
            os.remove(os.path.join(backup_dir, old))
            _logger.info("Rotated old backup: %s", old)
        except Exception as e:
            _logger.warning("Could not remove old backup %s: %s", old, e)


def create_auto_backup() -> str | None:
    """
    Create an automatic backup if the last backup is older than 24h.
    Returns the filename of the created backup, or None if skipped.
    """
    db_path = _get_db_path()
    if not db_path or not os.path.isdir(db_path):
        _logger.warning("Auto-backup: SP5_DB_PATH not set or not a directory, skipping.")
        return None

    backup_dir = _get_backup_dir()
    if not backup_dir:
        return None

    # Check if last backup is younger than 24h
    existing = sorted(
        [f for f in os.listdir(backup_dir) if f.startswith('sp5_backup_') and f.endswith('.zip')],
        reverse=True
    )
    if existing:
        newest = existing[0]
        newest_path = os.path.join(backup_dir, newest)
        age_hours = (
            _backup_dt.now() - _backup_dt.fromtimestamp(os.path.getmtime(newest_path))
        ).total_seconds() / 3600
        if age_hours < 24:
            _logger.info("Auto-backup: last backup is %.1fh old, skipping.", age_hours)
            return None

    ts = _backup_dt.now().strftime('%Y%m%d_%H%M%S')
    filename = f"sp5_backup_{ts}.zip"
    dest = os.path.join(backup_dir, filename)

    try:
        data = _create_zip_bytes(db_path)
        with open(dest, 'wb') as f:
            f.write(data)
        _rotate_backups(backup_dir)
        _logger.info("Auto-backup created: %s (%d bytes)", filename, len(data))
        return filename
    except Exception as e:
        _logger.error("Auto-backup failed: %s", e)
        return None


@router.get("/api/admin/backups", tags=["Backup"], summary="List database backups")
def list_backups(_admin: dict = Depends(require_admin)):
    """List all server-side backups. Admin only."""
    backup_dir = _get_backup_dir()
    if not backup_dir:
        return {"backups": [], "backup_dir": None}

    files = sorted(
        [f for f in os.listdir(backup_dir) if f.startswith('sp5_backup_') and f.endswith('.zip')],
        reverse=True
    )

    result = []
    for fname in files:
        fpath = os.path.join(backup_dir, fname)
        try:
            stat = os.stat(fpath)
            result.append({
                "filename": fname,
                "size_bytes": stat.st_size,
                "created_at": _backup_dt.fromtimestamp(stat.st_mtime).isoformat(),
            })
        except Exception:
            pass

    return {"backups": result, "backup_dir": backup_dir}


@router.get("/api/admin/backups/{filename}/download", tags=["Backup"], summary="Download database backup")
def download_saved_backup(filename: str, _admin: dict = Depends(require_admin)):
    """Download a specific saved backup by filename. Admin only."""
    # Security: only allow safe filenames
    if not filename.startswith('sp5_backup_') or not filename.endswith('.zip') or '/' in filename or '..' in filename:
        raise HTTPException(status_code=400, detail="Ungültiger Dateiname")

    backup_dir = _get_backup_dir()
    if not backup_dir:
        raise HTTPException(status_code=500, detail="Backup-Verzeichnis nicht konfiguriert")

    fpath = os.path.join(backup_dir, filename)
    if not os.path.isfile(fpath):
        raise HTTPException(status_code=404, detail="Backup nicht gefunden")

    with open(fpath, 'rb') as f:
        data = f.read()

    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/api/admin/backups/{filename}", tags=["Backup"], summary="Delete database backup")
def delete_saved_backup(filename: str, _admin: dict = Depends(require_admin)):
    """Delete a specific saved backup. Admin only."""
    if not filename.startswith('sp5_backup_') or not filename.endswith('.zip') or '/' in filename or '..' in filename:
        raise HTTPException(status_code=400, detail="Ungültiger Dateiname")

    backup_dir = _get_backup_dir()
    if not backup_dir:
        raise HTTPException(status_code=500, detail="Backup-Verzeichnis nicht konfiguriert")

    fpath = os.path.join(backup_dir, filename)
    if not os.path.isfile(fpath):
        raise HTTPException(status_code=404, detail="Backup nicht gefunden")

    os.remove(fpath)
    return {"ok": True, "deleted": filename}


@router.get("/api/backup/download", tags=["Backup"], summary="Download current database backup")
def backup_download(_admin: dict = Depends(require_admin)):
    """Create a ZIP of all .DBF / .FPT / .CDX files and return as download. Also saves to backup dir."""
    db_path = _get_db_path()
    if not db_path or not os.path.isdir(db_path):
        raise HTTPException(status_code=500, detail=f"SP5_DB_PATH not set or invalid: {db_path!r}")

    data = _create_zip_bytes(db_path)

    # Also persist to server-side backup dir
    backup_dir = _get_backup_dir()
    if backup_dir:
        ts_save = _backup_dt.now().strftime('%Y%m%d_%H%M%S')
        dest = os.path.join(backup_dir, f"sp5_backup_{ts_save}.zip")
        try:
            with open(dest, 'wb') as f:
                f.write(data)
            _rotate_backups(backup_dir)
        except Exception as e:
            _logger.warning("Could not save backup to disk: %s", e)

    ts = _backup_dt.now().strftime('%Y%m%d_%H%M')
    filename = f"sp5_backup_{ts}.zip"

    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/backup/restore", tags=["Backup"], summary="Restore database from backup")
async def backup_restore(file: UploadFile = File(...), _admin: dict = Depends(require_admin)):
    """Restore .DBF / .FPT / .CDX files from an uploaded ZIP.

    ⚠️  DESTRUCTIVE OPERATION: This endpoint overwrites existing database files on disk.
    All current data will be replaced by the contents of the uploaded ZIP.
    There is no automatic rollback. Make sure you have a backup before restoring.
    """
    allowed_ext = {'.DBF', '.FPT', '.CDX'}

    db_path_restore = _get_db_path()
    if not db_path_restore or not os.path.isdir(db_path_restore):
        raise HTTPException(status_code=500, detail=f"SP5_DB_PATH not set or invalid: {db_path_restore!r}")

    _logger.warning(
        "BACKUP RESTORE initiated: filename=%s size=%s — this will overwrite DB files in %s",
        file.filename, file.size, db_path_restore
    )

    content = await file.read()

    # Enforce maximum upload size (50 MB)
    MAX_UPLOAD_BYTES = 50 * 1024 * 1024
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Upload zu groß. Maximum: {MAX_UPLOAD_BYTES // (1024*1024)} MB"
        )

    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Ungültige ZIP-Datei")

    names_in_zip = zf.namelist()
    dbf_files = [n for n in names_in_zip if os.path.splitext(n)[1].upper() == '.DBF']
    if not dbf_files:
        raise HTTPException(status_code=400, detail="ZIP enthält keine .DBF Dateien")

    safe_db_path = os.path.abspath(db_path_restore)
    restored: list[str] = []
    with zf:
        for name in names_in_zip:
            ext = os.path.splitext(name)[1].upper()
            if ext not in allowed_ext:
                continue
            basename = os.path.basename(name)
            if not basename:
                continue
            # Extra safety: ensure the resolved destination is inside DB_PATH
            dest = os.path.normpath(os.path.join(safe_db_path, basename))
            if not dest.startswith(safe_db_path + os.sep) and dest != safe_db_path:
                # Should never happen since basename has no path separators, but
                # guard against exotic os.path.join edge cases on all platforms.
                continue
            data = zf.read(name)
            with open(dest, 'wb') as fout:
                fout.write(data)
            restored.append(basename)

    _logger.warning("BACKUP RESTORE completed: %d files restored: %s", len(restored), restored)
    return {"restored": len(restored), "files": restored}


# ── Admin: Compact database ───────────────────────────────────────────────────

@router.post("/api/admin/compact", tags=["Admin"], summary="Compact database (PACK)")
def compact_database(_cur_user: dict = Depends(require_admin)):
    """
    Compact all .DBF files in SP5_DB_PATH by rewriting them without deleted records.
    Deleted records have 0x2A ('*') as the first byte of their data row.
    Each file is exclusively locked during the operation to prevent concurrent corruption.
    Returns a summary of files processed and records removed.
    """
    import struct as _struct
    import fcntl as _fcntl
    from datetime import date as _date

    db_path = os.environ.get('SP5_DB_PATH', '')
    if not db_path or not os.path.isdir(db_path):
        raise HTTPException(status_code=500, detail=f"SP5_DB_PATH not set or not a directory: {db_path!r}")

    dbf_files = [f for f in os.listdir(db_path) if f.upper().endswith('.DBF')]
    results = []
    total_removed = 0

    for fname in sorted(dbf_files):
        fpath = os.path.join(db_path, fname)
        try:
            # Open for read+write and hold an exclusive lock for the entire
            # read-modify-write cycle to prevent concurrent write corruption.
            with open(fpath, 'r+b') as f:
                _fcntl.flock(f.fileno(), _fcntl.LOCK_EX)
                try:
                    raw = f.read()

                    if len(raw) < 32:
                        results.append({'file': fname, 'skipped': 'too small / corrupt'})
                        continue

                    # Parse DBF header
                    num_records = _struct.unpack_from('<I', raw, 4)[0]
                    header_size = _struct.unpack_from('<H', raw, 8)[0]
                    record_size = _struct.unpack_from('<H', raw, 10)[0]

                    if record_size == 0:
                        results.append({'file': fname, 'skipped': 'record_size=0'})
                        continue

                    # Separate header bytes from record area
                    header_bytes = bytearray(raw[:header_size])
                    records_area = raw[header_size:]

                    # Remove trailing EOF marker for processing
                    if records_area and records_area[-1] == 0x1A:
                        records_area = records_area[:-1]

                    # Split into individual records and filter out deleted ones
                    active_records = []
                    deleted_count = 0
                    for i in range(num_records):
                        start = i * record_size
                        end = start + record_size
                        if end > len(records_area):
                            break
                        rec = records_area[start:end]
                        if rec[0:1] == b'\x2a':  # deleted marker
                            deleted_count += 1
                        else:
                            active_records.append(rec)

                    if deleted_count == 0:
                        results.append({'file': fname, 'removed': 0, 'active': len(active_records)})
                        continue

                    # Update header: new record count + today's date
                    today = _date.today()
                    header_bytes[1] = today.year % 100
                    header_bytes[2] = today.month
                    header_bytes[3] = today.day
                    _struct.pack_into('<I', header_bytes, 4, len(active_records))

                    # Write compacted file (truncate then rewrite)
                    f.seek(0)
                    f.truncate()
                    f.write(bytes(header_bytes))
                    for rec in active_records:
                        f.write(rec)
                    f.write(b'\x1a')  # EOF marker
                    f.flush()
                finally:
                    _fcntl.flock(f.fileno(), _fcntl.LOCK_UN)

            total_removed += deleted_count
            results.append({'file': fname, 'removed': deleted_count, 'active': len(active_records)})

        except Exception as e:
            results.append({'file': fname, 'error': str(e)})

    return {
        'ok': True,
        'files_processed': len(results),
        'total_records_removed': total_removed,
        'details': results,
    }


# ── Frontend Error Reporting ──────────────────────────────────

_FRONTEND_ERRORS_FILE = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'frontend_errors.json')

def _load_frontend_errors() -> list:
    os.makedirs(os.path.dirname(_FRONTEND_ERRORS_FILE), exist_ok=True)
    if not os.path.exists(_FRONTEND_ERRORS_FILE):
        return []
    with open(_FRONTEND_ERRORS_FILE, 'r', encoding='utf-8') as f:
        try:
            return json.load(f)
        except Exception:
            return []

def _save_frontend_errors(errors: list):
    os.makedirs(os.path.dirname(_FRONTEND_ERRORS_FILE), exist_ok=True)
    with open(_FRONTEND_ERRORS_FILE, 'w', encoding='utf-8') as f:
        json.dump(errors, f, ensure_ascii=False, indent=2)


class FrontendErrorReport(BaseModel):
    error: str = Field(..., max_length=2000)
    component_stack: Optional[str] = Field(None, max_length=5000)
    url: Optional[str] = Field(None, max_length=500)
    user_agent: Optional[str] = Field(None, max_length=300)
    timestamp: Optional[str] = Field(None, max_length=50)


@router.post("/api/errors", tags=["Health"], summary="Report frontend error")
@limiter.limit("10/minute")
def report_frontend_error(request: Request, body: FrontendErrorReport):
    """Receive a frontend error report and store it."""
    errors = _load_frontend_errors()
    entry = {
        "timestamp": body.timestamp or __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat().replace('+00:00', 'Z'),
        "error": body.error,
        "component_stack": body.component_stack,
        "url": body.url,
        "user_agent": body.user_agent,
        "client_ip": request.client.host if request.client else 'unknown',
    }
    errors.append(entry)
    # Keep last 500 errors
    if len(errors) > 500:
        errors = errors[-500:]
    _save_frontend_errors(errors)
    _logger.warning("Frontend error reported: %s", body.error[:200])
    return {"ok": True}


@router.get("/api/admin/frontend-errors", tags=["Health"], summary="List frontend errors (Admin)")
def get_frontend_errors(_cur_user: dict = Depends(require_admin)):
    """Return all stored frontend errors."""
    errors = _load_frontend_errors()
    return {"count": len(errors), "errors": errors[-100:]}  # last 100


@router.get("/api/admin/cache-stats", tags=["Admin"], summary="Cache statistics (Admin)")
def get_cache_stats(_cur_user: dict = Depends(require_admin)):
    """Return internal cache statistics. Admin only."""
    stats: dict = {}
    try:
        from sp5lib.cache import get_cache_stats as _get_cache_stats
        stats = _get_cache_stats()
    except Exception:
        try:
            from sp5lib.database import _cache as _dbf_cache
            stats = {"entries": len(_dbf_cache) if hasattr(_dbf_cache, '__len__') else -1}
        except Exception:
            stats = {"entries": -1, "error": "Cache not accessible"}
    return {"ok": True, "cache": stats}
