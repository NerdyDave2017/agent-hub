"""
Tenant persistence and lookups.

All functions take an **async** SQLAlchemy `Session` from the request scope; callers own
`commit`/`rollback` boundaries except where noted (e.g. `create_tenant` commits on success).

Raises **`domain.exceptions`** — no HTTP/FastAPI types here. No Pydantic request models —
pass plain values from routers after validation.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Tenant
from domain.exceptions import TenantNotFound, TenantSlugConflict


async def require_tenant(session: AsyncSession, tenant_id: UUID) -> Tenant:
    """Return the tenant row or raise `TenantNotFound`."""
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise TenantNotFound
    return tenant


async def list_tenants_page(
    session: AsyncSession,
    *,
    skip: int,
    limit: int,
) -> tuple[list[Tenant], int]:
    """Return `(rows, total_count)` for pagination."""
    total = int(await session.scalar(select(func.count()).select_from(Tenant)) or 0)
    rows = await session.scalars(
        select(Tenant).order_by(Tenant.created_at.desc()).offset(skip).limit(limit)
    )
    return list(rows.all()), total


async def create_tenant(session: AsyncSession, *, name: str, slug: str) -> Tenant:
    """
    Insert a new tenant. **Commits** on success; rolls back and raises `TenantSlugConflict`
    on slug conflict.
    """
    tenant = Tenant(name=name, slug=slug)
    session.add(tenant)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise TenantSlugConflict from None
    await session.refresh(tenant)
    return tenant


async def update_tenant(session: AsyncSession, tenant_id: UUID, *, name: str | None) -> Tenant:
    """Apply partial updates; **commits**."""
    tenant = await require_tenant(session, tenant_id)
    if name is not None:
        tenant.name = name
    await session.commit()
    await session.refresh(tenant)
    return tenant
