"""Read-only hub calls: tenant profile + recent incidents (Bearer ``HUB_SERVICE_TOKEN``)."""

import httpx

from incident_triage.graph.state import TriageState
from incident_triage.instrumentation.decorator import traced_node
from incident_triage.settings import get_settings


@traced_node("enrich")
async def run(state: TriageState) -> dict:
    s = get_settings()
    base = (s.hub_base_url or "").strip().rstrip("/")
    token = (s.hub_service_token or "").strip()
    if not base or not token:
        return {"tenant_context": {}, "prior_incidents": []}
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=base, timeout=30.0) as client:
        tenant_resp = await client.get(f"/internal/tenants/{state.tenant_id}", headers=headers)
        tenant_resp.raise_for_status()
        inc_resp = await client.get(
            f"/internal/tenants/{state.tenant_id}/incidents/recent",
            headers=headers,
            params={"limit": 5},
        )
        inc_resp.raise_for_status()
    return {
        "tenant_context": tenant_resp.json(),
        "prior_incidents": inc_resp.json(),
    }
