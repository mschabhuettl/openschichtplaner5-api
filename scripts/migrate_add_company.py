#!/usr/bin/env python3
"""
Migration: Add Company table and link existing employees to a default company.

Steps:
  1. Create the 'companies' table (if not exists).
  2. Add 'company_id' column to 'employees' (if not exists).
  3. Insert a default Company (id=1, name='Default', slug='default').
  4. Set company_id=1 for all employees that have NULL company_id.

Safe to run multiple times (idempotent).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the backend package is importable when run as a script.
_backend = Path(__file__).resolve().parent.parent
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from sp5lib.orm import get_engine, get_session  # noqa: E402
from sp5lib.orm.models import Company  # noqa: E402
from sqlalchemy import inspect, text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402


def migrate(database_url_or_engine: str | Engine | None = None) -> None:
    """Run the Company migration.

    Args:
        database_url_or_engine: Either a database URL string or an existing
            SQLAlchemy Engine instance. Defaults to ``sqlite:///sp5.db``.
    """
    if database_url_or_engine is None:
        engine = get_engine("sqlite:///sp5.db")
    elif isinstance(database_url_or_engine, Engine):
        engine = database_url_or_engine
    else:
        engine = get_engine(str(database_url_or_engine))

    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()

    # 1. Create companies table if missing
    if "companies" not in existing_tables:
        Company.__table__.create(engine)
        print("✔ Created table 'companies'")
    else:
        print("⏭ Table 'companies' already exists")

    # 2. Add company_id column to employees if missing
    if "employees" in existing_tables:
        columns = [c["name"] for c in inspector.get_columns("employees")]
        if "company_id" not in columns:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "ALTER TABLE employees ADD COLUMN company_id INTEGER "
                        "REFERENCES companies(id) ON DELETE SET NULL"
                    )
                )
            print("✔ Added column 'company_id' to 'employees'")
        else:
            print("⏭ Column 'company_id' already exists on 'employees'")

    # 3. Insert default company
    with get_session(engine) as session:
        default = session.get(Company, 1)
        if default is None:
            default = Company(id=1, name="Default", slug="default")
            session.add(default)
            session.commit()
            print("✔ Inserted default Company (id=1, name='Default')")
        else:
            print(f"⏭ Default company already exists: {default}")

    # 4. Backfill company_id for existing employees
    if "employees" in inspector.get_table_names():
        with get_session(engine) as session:
            result = session.execute(
                text("UPDATE employees SET company_id = 1 WHERE company_id IS NULL")
            )
            session.commit()
            print(f"✔ Updated {result.rowcount} employee(s) with company_id=1")


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else None
    migrate(url)
