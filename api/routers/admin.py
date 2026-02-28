"""Admin router: users, settings, backup, periods, admin tasks."""
import os
import io
import zipfile
import json
from datetime import datetime as _backup_dt
from fastapi import APIRouter, HTTPException, Query, Header, Depends, Request, UploadFile, File
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from ..dependencies import (
    get_db, require_admin, require_planer, require_auth, require_role,
    _sanitize_500, _logger, get_current_user,
)

router = APIRouter()



# ── Periods ───────────────────────────────────────────────────

@router.get("/api/periods")
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


@router.post("/api/periods")
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


@router.delete("/api/periods/{period_id}")
def delete_period(period_id: int, _cur_user: dict = Depends(require_planer)):
    try:
        count = get_db().delete_period(period_id)
        return {"ok": True, "deleted": count}
    except Exception as e:
        raise _sanitize_500(e)


# ── Settings (USETT) ─────────────────────────────────────────

@router.get("/api/settings")
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


@router.put("/api/settings")
def update_settings(body: SettingsUpdate, _cur_user: dict = Depends(require_admin)):
    """Update global settings in 5USETT.DBF."""
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        result = get_db().update_usett(data)
        return {"ok": True, "record": result}
    except Exception as e:
        raise _sanitize_500(e)


# ── Backup / Restore endpoints ───────────────────────────────

import zipfile
from datetime import datetime as _backup_dt
from fastapi.responses import StreamingResponse


@router.get("/api/backup/download")
def backup_download(_admin: dict = Depends(require_admin)):
    """Create a ZIP of all .DBF / .FPT / .CDX files and return as download. Admin only."""
    allowed_ext = {'.DBF', '.FPT', '.CDX'}

    buf = io.BytesIO()
    files_added: list[str] = []

    with zipfile.ZipFile(buf, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
        for fname in os.listdir(DB_PATH):
            ext = os.path.splitext(fname)[1].upper()
            if ext in allowed_ext:
                full_path = os.path.join(DB_PATH, fname)
                if os.path.isfile(full_path):
                    zf.write(full_path, arcname=fname)
                    files_added.append(fname)

    buf.seek(0)
    ts = _backup_dt.now().strftime('%Y%m%d_%H%M')
    filename = f"sp5_backup_{ts}.zip"

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/backup/restore")
async def backup_restore(file: UploadFile = File(...), _admin: dict = Depends(require_admin)):
    """Restore .DBF / .FPT / .CDX files from an uploaded ZIP.

    ⚠️  DESTRUCTIVE OPERATION: This endpoint overwrites existing database files on disk.
    All current data will be replaced by the contents of the uploaded ZIP.
    There is no automatic rollback. Make sure you have a backup before restoring.
    """
    allowed_ext = {'.DBF', '.FPT', '.CDX'}

    _logger.warning(
        "BACKUP RESTORE initiated: filename=%s size=%s — this will overwrite DB files in %s",
        file.filename, file.size, DB_PATH
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

    safe_db_path = os.path.abspath(DB_PATH)
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

@router.post("/api/admin/compact")
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
