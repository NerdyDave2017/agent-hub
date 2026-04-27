"""Gmail → Pub/Sub push receiver: verify token, enqueue ``gmail_history_sync`` (fast 200)."""

from __future__ import annotations

import base64
import hmac
import json
import uuid

from fastapi import APIRouter, Request
from sqlalchemy import select

from agent_hub_core.config.settings import get_settings
from agent_hub_core.db.models import Integration
from agent_hub_core.domain.enums import JobType
from agent_hub_core.observability.logging import get_logger

from apis.dependencies import DbSession
from services import jobs_service

log = get_logger(__name__)

router = APIRouter()


def _verify_pubsub_token(request: Request) -> bool:
    settings = get_settings()
    expected = (settings.google_webhook_secret or "").strip()
    if not expected:
        return True
    token = request.query_params.get("token", "")
    return hmac.compare_digest(token, expected)


@router.post("/push")
async def gmail_pubsub_push(request: Request, session: DbSession) -> dict[str, str]:
    """
    Google Pub/Sub push target. Decode ``emailAddress`` + ``historyId``, advance cursor,
    enqueue ``gmail_history_sync``. Always return **200** body for Pub/Sub ack semantics.
    """
    if not _verify_pubsub_token(request):
        log.warning("gmail_pubsub_push_invalid_token")
        return {"status": "ignored"}

    try:
        body = await request.json()
        inner = body.get("message") or {}
        raw_b64 = inner.get("data") or ""
        if not raw_b64:
            return {"status": "malformed"}
        decoded = base64.b64decode(raw_b64).decode("utf-8")
        data = json.loads(decoded)
        email_addr = data["emailAddress"]
        history_id = str(data["historyId"])
    except Exception as exc:
        log.error("gmail_pubsub_push_decode_failed", error=str(exc))
        return {"status": "malformed"}

    integration = await session.scalar(
        select(Integration).where(
            Integration.email_address == email_addr,
            Integration.provider == "gmail",
            Integration.connection_status == "active",
            Integration.watch_active.is_(True),
        )
    )
    if integration is None:
        log.warning("gmail_pubsub_unknown_mailbox", email_address=email_addr)
        return {"status": "unknown"}

    if integration.agent_id is None:
        log.error("gmail_pubsub_missing_agent_id", integration_id=str(integration.id))
        return {"status": "misconfigured"}

    old_hist = integration.last_history_id
    integration.last_history_id = history_id
    await session.commit()
    await session.refresh(integration)

    cid = str(uuid.uuid4())
    await jobs_service.create_job_with_publish(
        session,
        tenant_id=integration.tenant_id,
        job_type=JobType.gmail_history_sync.value,
        correlation_id=cid,
        agent_id=integration.agent_id,
        idempotency_key=f"gmail_hist:{integration.id}:{history_id}",
        payload={
            "integration_id": str(integration.id),
            "email_address": email_addr,
            "start_history_id": old_hist,
            "new_history_id": history_id,
        },
    )

    log.info(
        "gmail_history_sync_enqueued",
        integration_id=str(integration.id),
        tenant_id=str(integration.tenant_id),
        history_id=history_id,
    )
    return {"status": "accepted"}
