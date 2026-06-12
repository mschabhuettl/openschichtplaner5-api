"""Differenzierte Sichtbarkeit (Spec 9.5.3, 5GRACC/5EMACC).

Stellt FastAPI-Dependencies bereit, mit denen Listen-/Plan-Endpoints auf die
für den angemeldeten Benutzer sichtbaren Mitarbeiter/Gruppen eingeschränkt
werden. Admin und Benutzer ohne differenzierte Festlegung sehen alles (``None``).
"""

from fastapi import Depends

from .dependencies import get_current_user, get_db


def visible_employee_ids(
    user: dict | None = Depends(get_current_user),
) -> set[int] | None:
    """Sichtbare Mitarbeiter-IDs des aktuellen Benutzers, oder ``None`` =
    unbeschränkt (Admin/volle Rechte ohne differenzierte Festlegung)."""
    if user is None or user.get("role") == "Admin":
        return None
    uid = user.get("ID")
    if not uid:
        return None
    try:
        return get_db().get_user_visible_employee_ids(uid)
    except Exception:
        return None


def visible_group_ids(
    user: dict | None = Depends(get_current_user),
) -> set[int] | None:
    """Sichtbare Gruppen-IDs des aktuellen Benutzers, oder ``None`` = unbeschränkt."""
    if user is None or user.get("role") == "Admin":
        return None
    uid = user.get("ID")
    if not uid:
        return None
    try:
        return get_db().get_user_visible_group_ids(uid)
    except Exception:
        return None


def filter_by_employee_scope(
    rows: list[dict], scope: set[int] | None, key: str = "ID"
) -> list[dict]:
    """Filtere eine Mitarbeiter-Liste auf die sichtbare Menge (``None`` = alle)."""
    if scope is None:
        return rows
    return [r for r in rows if r.get(key) in scope]
