"""Aggregate versioned API routers."""

from fastapi import APIRouter

from apis import agents, jobs, tenants

api_router = APIRouter()
api_router.include_router(tenants.router, prefix="/tenants", tags=["tenants"])
api_router.include_router(agents.router, prefix="/tenants/{tenant_id}/agents", tags=["agents"])
api_router.include_router(jobs.router, prefix="/tenants/{tenant_id}/jobs", tags=["jobs"])
