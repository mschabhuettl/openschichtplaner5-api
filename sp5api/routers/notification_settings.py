"""Notification settings per user — which events trigger email notifications.

Stored in backend/data/notification_settings.json keyed by user_id.
"""

import json
import os
import threading

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..dependencies import require_auth

router = APIRouter()

_SETTINGS_FILE = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "notification_settings.json"
)
_lock = threading.Lock()

# Default: all event types enabled
DEFAULT_SETTINGS = {
    "shift_assigned": True,
    "shift_changed": True,
    "swap_requested": True,
    "swap_approved": True,
    "swap_rejected": True,
    "vacation_approved": True,
    "vacation_rejected": True,
    "schedule_comment_added": True,
}


class NotificationSettingsUpdate(BaseModel):
    shift_assigned: bool = True
    shift_changed: bool = True
    swap_requested: bool = True
    swap_approved: bool = True
    swap_rejected: bool = True
    vacation_approved: bool = True
    vacation_rejected: bool = True
    schedule_comment_added: bool = True


def _load_all() -> dict:
    try:
        if os.path.exists(_SETTINGS_FILE):
            with open(_SETTINGS_FILE, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_all(data: dict) -> None:
    os.makedirs(os.path.dirname(_SETTINGS_FILE), exist_ok=True)
    import tempfile

    dir_ = os.path.dirname(os.path.abspath(_SETTINGS_FILE))
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=dir_, delete=False, suffix=".tmp"
    ) as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp_path = tmp.name
    os.replace(tmp_path, _SETTINGS_FILE)


def get_user_settings(user_id: int) -> dict:
    """Return settings for a user, merging with defaults."""
    all_settings = _load_all()
    user_key = str(user_id)
    stored = all_settings.get(user_key, {})
    return {**DEFAULT_SETTINGS, **stored}


@router.get("/api/notifications/settings", tags=["Notifications"])
def get_notification_settings(current_user=Depends(require_auth)):
    """Get the current user's notification settings."""
    user_id = current_user.get("ID") or current_user.get("id")
    with _lock:
        settings = get_user_settings(user_id)
    return {"user_id": user_id, "settings": settings}


@router.put("/api/notifications/settings", tags=["Notifications"])
def update_notification_settings(
    payload: NotificationSettingsUpdate,
    current_user=Depends(require_auth),
):
    """Update the current user's notification settings."""
    user_id = current_user.get("ID") or current_user.get("id")
    user_key = str(user_id)
    with _lock:
        all_settings = _load_all()
        all_settings[user_key] = payload.model_dump()
        _save_all(all_settings)
        settings = get_user_settings(user_id)
    return {"user_id": user_id, "settings": settings, "updated": True}
