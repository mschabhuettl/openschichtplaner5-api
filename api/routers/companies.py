"""Company management router — multi-tenant CRUD."""

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from ..dependencies import (
    _logger,
    _sanitize_500,
    require_admin,
)

router = APIRouter(prefix="/api/companies", tags=["Companies"])


# ── Helpers ──────────────────────────────────────────────────

def _get_orm_session():
    """Get an ORM session connected to the app's SQLite DB."""
    import os  # noqa: I001

    from sp5lib.orm import get_engine, init_db
    from sp5lib.orm.base import get_session

    import api.main as _main

    # Derive the ORM database path from the DBF DB_PATH
    # The SQLite DB sits alongside the DBF data directory
    orm_db = os.path.join(os.path.dirname(_main.DB_PATH), "sp5_orm.db")
    engine = get_engine(f"sqlite:///{orm_db}")
    init_db(engine)
    return get_session(engine), engine


def _slugify(name: str) -> str:
    """Generate a URL-safe slug from a company name."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s-]+", "-", slug)
    return slug.strip("-") or "company"


# ── Schemas ──────────────────────────────────────────────────

class CompanyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200, description="Company name")
    slug: str | None = Field(None, max_length=200, description="URL-safe slug (auto-generated if omitted)")

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Firmenname darf nicht leer sein")
        return v.strip()


class CompanyUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    slug: str | None = Field(None, max_length=200)
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("Firmenname darf nicht leer sein")
        return v.strip() if v is not None else v


class CompanyResponse(BaseModel):
    id: int
    name: str
    slug: str
    is_active: bool
    employee_count: int = 0
    group_count: int = 0

    model_config = {"from_attributes": True}


# ── Endpoints ────────────────────────────────────────────────

def _is_super_admin(user: dict) -> bool:
    """Check if user has super-admin privileges (can see all companies)."""
    return user.get("role") == "Admin" and user.get("company_id") is None


@router.get("", response_model=list[CompanyResponse])
def list_companies(user: dict = Depends(require_admin)):
    """List all companies. Super-admins see all; regular admins see only their own."""
    from sp5lib.orm.models import Company

    session, engine = _get_orm_session()
    try:
        query = session.query(Company)
        if not _is_super_admin(user):
            company_id = user.get("company_id")
            if company_id:
                query = query.filter(Company.id == company_id)
            else:
                # No company assigned — show all (backward compat)
                pass
        companies = query.order_by(Company.name).all()
        result = []
        for c in companies:
            result.append(CompanyResponse(
                id=c.id,
                name=c.name,
                slug=c.slug,
                is_active=c.is_active,
                employee_count=len(c.employees),
                group_count=len(c.groups),
            ))
        return result
    except Exception as e:
        raise _sanitize_500(e, "list_companies")
    finally:
        session.close()


@router.get("/{company_id}", response_model=CompanyResponse)
def get_company(company_id: int, user: dict = Depends(require_admin)):
    """Get a single company by ID."""
    from sp5lib.orm.models import Company

    session, engine = _get_orm_session()
    try:
        company = session.get(Company, company_id)
        if not company:
            raise HTTPException(status_code=404, detail=f"Firma ID {company_id} nicht gefunden")

        # Non-super-admins can only see their own company
        if not _is_super_admin(user) and user.get("company_id") and user.get("company_id") != company_id:
            raise HTTPException(status_code=403, detail="Kein Zugriff auf diese Firma")

        return CompanyResponse(
            id=company.id,
            name=company.name,
            slug=company.slug,
            is_active=company.is_active,
            employee_count=len(company.employees),
            group_count=len(company.groups),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _sanitize_500(e, "get_company")
    finally:
        session.close()


@router.post("", response_model=CompanyResponse, status_code=201)
def create_company(body: CompanyCreate, user: dict = Depends(require_admin)):
    """Create a new company. Only super-admins can create companies."""
    from sp5lib.orm.models import Company
    from sqlalchemy.exc import IntegrityError

    session, engine = _get_orm_session()
    try:
        slug = body.slug or _slugify(body.name)

        company = Company(name=body.name, slug=slug)
        session.add(company)
        session.commit()

        _logger.warning(
            "AUDIT COMPANY_CREATE | user=%s name=%s slug=%s id=%s",
            user.get("NAME"), body.name, slug, company.id,
        )

        return CompanyResponse(
            id=company.id,
            name=company.name,
            slug=company.slug,
            is_active=company.is_active,
            employee_count=0,
            group_count=0,
        )
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Firma mit Name '{body.name}' oder Slug '{slug}' existiert bereits",
        )
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise _sanitize_500(e, "create_company")
    finally:
        session.close()


@router.put("/{company_id}", response_model=CompanyResponse)
def update_company(company_id: int, body: CompanyUpdate, user: dict = Depends(require_admin)):
    """Update company details."""
    from sp5lib.orm.models import Company
    from sqlalchemy.exc import IntegrityError

    session, engine = _get_orm_session()
    try:
        company = session.get(Company, company_id)
        if not company:
            raise HTTPException(status_code=404, detail=f"Firma ID {company_id} nicht gefunden")

        if body.name is not None:
            company.name = body.name
        if body.slug is not None:
            company.slug = body.slug
        if body.is_active is not None:
            company.is_active = body.is_active

        session.commit()

        _logger.warning(
            "AUDIT COMPANY_UPDATE | user=%s company_id=%d",
            user.get("NAME"), company_id,
        )

        return CompanyResponse(
            id=company.id,
            name=company.name,
            slug=company.slug,
            is_active=company.is_active,
            employee_count=len(company.employees),
            group_count=len(company.groups),
        )
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Name oder Slug bereits vergeben")
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise _sanitize_500(e, "update_company")
    finally:
        session.close()


@router.delete("/{company_id}")
def delete_company(company_id: int, user: dict = Depends(require_admin)):
    """Deactivate a company (soft-delete). Cannot delete the default company (id=1)."""
    from sp5lib.orm.models import Company

    if company_id == 1:
        raise HTTPException(status_code=400, detail="Die Standard-Firma kann nicht gelöscht werden")

    session, engine = _get_orm_session()
    try:
        company = session.get(Company, company_id)
        if not company:
            raise HTTPException(status_code=404, detail=f"Firma ID {company_id} nicht gefunden")

        company.is_active = False
        session.commit()

        _logger.warning(
            "AUDIT COMPANY_DELETE | user=%s company_id=%d name=%s",
            user.get("NAME"), company_id, company.name,
        )

        return {"ok": True, "deactivated": company_id}
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise _sanitize_500(e, "delete_company")
    finally:
        session.close()
