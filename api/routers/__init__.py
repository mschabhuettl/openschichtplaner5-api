"""API Routers package."""

from . import absences, admin, auth, employees, master_data, misc, reports, schedule

__all__ = [
    "auth",
    "employees",
    "schedule",
    "absences",
    "master_data",
    "reports",
    "admin",
    "misc",
]
