"""In-app notification system for OpenSchichtplaner5.

Notifications are stored in a JSON file (notifications.json) alongside
other persistence files. Each notification has:
  id, recipient_employee_id (None = all planners/admins), type, title,
  message, read, created_at, link (optional).
"""
import os
import json
import time
import threading
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
from ..dependencies import get_db, require_planer, _sanitize_500

router = APIRouter()

_NOTIF_FILE = os.path.join(os.path.dirname(__file__), '..', 'notifications.json')
_lock = threading.Lock()


# ── Storage helpers ───────────────────────────────────────────────────────────

def _load() -> list:
    try:
        if os.path.exists(_NOTIF_FILE):
            with open(_NOTIF_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return []


def _save(data: list) -> None:
    try:
        with open(_NOTIF_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _next_id(data: list) -> int:
    return max((n['id'] for n in data), default=0) + 1


# ── Public helper: called by other routers ───────────────────────────────────

def create_notification(
    *,
    type: str,
    title: str,
    message: str,
    recipient_employee_id: Optional[int] = None,
    link: Optional[str] = None,
) -> dict:
    """Create and persist a notification. Thread-safe."""
    with _lock:
        data = _load()
        entry = {
            'id': _next_id(data),
            'type': type,
            'title': title,
            'message': message,
            'recipient_employee_id': recipient_employee_id,
            'link': link,
            'read': False,
            'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        }
        data.append(entry)
        _save(data)
        return entry


# ── API endpoints ─────────────────────────────────────────────────────────────

@router.get("/api/notifications", tags=["Notifications"], summary="List notifications")
def list_notifications(
    employee_id: Optional[int] = Query(None, description="Filter by recipient employee id (0 = planner-wide)"),
    unread_only: bool = Query(False),
    limit: int = Query(50, le=200),
    _cur_user: dict = Depends(require_planer),
):
    """Return notifications, newest first.

    - employee_id=<id>: employee-specific notifications for that person
    - employee_id omitted: returns planner-wide notifications (recipient_employee_id=None)
    - unread_only=true: filter to unread only
    """
    data = _load()
    if employee_id is not None:
        data = [n for n in data if n.get('recipient_employee_id') == employee_id]
    else:
        data = [n for n in data if n.get('recipient_employee_id') is None]
    if unread_only:
        data = [n for n in data if not n.get('read')]
    data = sorted(data, key=lambda n: n.get('created_at', ''), reverse=True)[:limit]
    return {"notifications": data, "count": len(data)}


@router.get("/api/notifications/all", tags=["Notifications"], summary="List all notifications (admin)")
def list_all_notifications(
    unread_only: bool = Query(False),
    limit: int = Query(100, le=500),
    _cur_user: dict = Depends(require_planer),
):
    """Return all notifications (for admin/planner overview)."""
    data = _load()
    if unread_only:
        data = [n for n in data if not n.get('read')]
    data = sorted(data, key=lambda n: n.get('created_at', ''), reverse=True)[:limit]
    return {"notifications": data, "count": len(data)}


@router.patch("/api/notifications/{notif_id}/read", tags=["Notifications"], summary="Mark notification as read")
def mark_read(notif_id: int, _cur_user: dict = Depends(require_planer)):
    """Mark a single notification as read."""
    with _lock:
        data = _load()
        for n in data:
            if n['id'] == notif_id:
                n['read'] = True
                _save(data)
                return {"ok": True}
    raise HTTPException(status_code=404, detail="Notification not found")


@router.patch("/api/notifications/read-all", tags=["Notifications"], summary="Mark all notifications as read")
def mark_all_read(
    employee_id: Optional[int] = Query(None),
    _cur_user: dict = Depends(require_planer),
):
    """Mark all (optionally filtered by recipient) notifications as read."""
    with _lock:
        data = _load()
        count = 0
        for n in data:
            if employee_id is not None:
                if n.get('recipient_employee_id') == employee_id and not n['read']:
                    n['read'] = True
                    count += 1
            else:
                if n.get('recipient_employee_id') is None and not n['read']:
                    n['read'] = True
                    count += 1
        _save(data)
    return {"ok": True, "marked": count}


@router.delete("/api/notifications/{notif_id}", tags=["Notifications"], summary="Delete notification")
def delete_notification(notif_id: int, _cur_user: dict = Depends(require_planer)):
    """Delete a notification."""
    with _lock:
        data = _load()
        new_data = [n for n in data if n['id'] != notif_id]
        if len(new_data) == len(data):
            raise HTTPException(status_code=404, detail="Notification not found")
        _save(new_data)
    return {"ok": True}
