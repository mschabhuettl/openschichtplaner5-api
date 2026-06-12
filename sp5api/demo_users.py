"""Idempotent demo-user bootstrap.

When ``SP5_SEED_DEMO_USERS`` is truthy (dev / demo / stack deployments — never
production), the API ensures the three documented demo accounts exist on
startup so that the credentials from the README work out of the box:

    admin  / Test1234  → role Admin
    planer / Test1234  → role Planer
    leser  / Test1234  → role Leser

Existing accounts are left untouched (matched case-insensitively by username);
only missing ones are created. Real deployments keep this flag off and use
their own 5USER accounts.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("sp5.api")

DEMO_PASSWORD = "Test1234"
DEMO_USERS = (
    ("admin", "Admin"),
    ("planer", "Planer"),
    ("leser", "Leser"),
)


_TRUTHY = {"1", "true", "yes", "on"}


def demo_seeding_enabled() -> bool:
    """Seed demo users when explicitly requested, or implicitly in dev mode.

    Dev mode already marks a demo/testing deployment, so `make dev` and the
    dev-mode stack get working demo logins without extra configuration.
    Production (no dev mode, no flag) never seeds.
    """
    if os.environ.get("SP5_SEED_DEMO_USERS", "").strip().lower() in _TRUTHY:
        return True
    return os.environ.get("SP5_DEV_MODE", "").strip().lower() in _TRUTHY


def ensure_demo_users(db) -> list[str]:
    """Upsert the demo accounts. Returns the list of newly *created* usernames.

    Missing accounts are created; an account that already exists under the same
    (case-insensitive) name has its password reset to ``Test1234`` and its role
    aligned — so the documented demo credentials reliably work even when the
    database already ships an account such as the original empty-password
    ``Admin``. Only effective when ``SP5_SEED_DEMO_USERS`` is enabled.
    """
    try:
        by_name = {(u.get("NAME") or "").strip().lower(): u for u in db.get_users()}
    except Exception as exc:  # noqa: BLE001 — bootstrap must never break startup
        logger.warning("Demo-user bootstrap skipped (cannot read users): %s", exc)
        return []

    created: list[str] = []
    for username, role in DEMO_USERS:
        existing = by_name.get(username.lower())
        try:
            if existing is None:
                db.create_user({"NAME": username, "PASSWORD": DEMO_PASSWORD, "role": role})
                created.append(username)
            else:
                uid = existing.get("ID")
                db.update_user(uid, {"role": role})
                db.change_password(uid, DEMO_PASSWORD)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Demo-user bootstrap: could not upsert %r: %s", username, exc)
    if created:
        logger.info("Demo users seeded: %s", ", ".join(created))
    return created
