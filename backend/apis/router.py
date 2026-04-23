"""Aggregate versioned API routers."""

from fastapi import APIRouter

from apis import agents, dashboard, integrations_gmail, integrations_slack, jobs, tenants

api_router = APIRouter()
# Register more specific `/tenants/{tenant_id}/dashboard/*` before `/tenants/{tenant_id}` routes.
api_router.include_router(
    dashboard.router,
    prefix="/tenants/{tenant_id}/dashboard",
    tags=["dashboard"],
)
api_router.include_router(tenants.router, prefix="/tenants", tags=["tenants"])
api_router.include_router(agents.router, prefix="/tenants/{tenant_id}/agents", tags=["agents"])
api_router.include_router(jobs.router, prefix="/tenants/{tenant_id}/jobs", tags=["jobs"])
api_router.include_router(integrations_gmail.router, tags=["integrations"])
api_router.include_router(integrations_slack.router, tags=["integrations"])
