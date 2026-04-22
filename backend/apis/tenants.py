from uuid import UUID

from fastapi import APIRouter, Query, status

from apis.dependencies import DbSession
from schemas.common import PaginatedMeta, PaginatedResponse
from schemas.tenant import TenantCreate, TenantRead, TenantUpdate
from services import tenants_service

router = APIRouter()


@router.get("", response_model=PaginatedResponse[TenantRead])
async def list_tenants(
    session: DbSession,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=200),
) -> PaginatedResponse[TenantRead]:
    rows, total = await tenants_service.list_tenants_page(session, skip=skip, limit=limit)
    items = [TenantRead.model_validate(r) for r in rows]
    return PaginatedResponse[TenantRead](
        items=items,
        meta=PaginatedMeta(total=total, skip=skip, limit=limit),
    )


@router.post("", response_model=TenantRead, status_code=status.HTTP_201_CREATED)
async def create_tenant(session: DbSession, body: TenantCreate) -> TenantRead:
    tenant = await tenants_service.create_tenant(session, name=body.name, slug=body.slug)
    return TenantRead.model_validate(tenant)


@router.get("/{tenant_id}", response_model=TenantRead)
async def get_tenant(session: DbSession, tenant_id: UUID) -> TenantRead:
    tenant = await tenants_service.require_tenant(session, tenant_id)
    return TenantRead.model_validate(tenant)


@router.patch("/{tenant_id}", response_model=TenantRead)
async def update_tenant(session: DbSession, tenant_id: UUID, body: TenantUpdate) -> TenantRead:
    tenant = await tenants_service.update_tenant(session, tenant_id, name=body.name)
    return TenantRead.model_validate(tenant)
