"""Renew Gmail ``users.watch`` before expiry (scheduled / manual job)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import Any
from uuid import UUID

import boto3
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_hub_core.config.settings import get_settings
from agent_hub_core.db.job_transitions import claim_job_for_worker, complete_job_success, fail_job_while_running
from agent_hub_core.db.models import Integration, Job
from agent_hub_core.domain.enums import JobStatus
from agent_hub_core.observability.logging import get_logger

from worker.handlers._idempotency import is_terminal_job
from worker.handlers.base import AbstractJobHandler

log = get_logger(__name__)

_RUNNING = "gmail_watch_renewal_running"
_DONE = "gmail_watch_renewal_complete"


def _sm_client():
    s = get_settings()
    kw: dict[str, Any] = {"region_name": s.aws_region}
    if s.aws_endpoint_url:
        kw["endpoint_url"] = s.aws_endpoint_url
    if s.aws_access_key_id and s.aws_secret_access_key:
        kw["aws_access_key_id"] = s.aws_access_key_id
        kw["aws_secret_access_key"] = s.aws_secret_access_key
    return boto3.client("secretsmanager", **kw)


def _sm_get_json(secret_arn: str) -> dict[str, Any]:
    sm = _sm_client()
    raw = sm.get_secret_value(SecretId=secret_arn)["SecretString"]
    return json.loads(raw)


def _persist_token(secret_arn: str, credentials: Credentials) -> None:
    sm = _sm_client()
    existing = json.loads(sm.get_secret_value(SecretId=secret_arn)["SecretString"])
    existing["access_token"] = credentials.token
    sm.put_secret_value(SecretId=secret_arn, SecretString=json.dumps(existing))


def _renew_watch_sync(*, secret_arn: str, topic_name: str) -> dict[str, Any]:
    secret = _sm_get_json(secret_arn)
    creds = Credentials(
        token=secret.get("access_token"),
        refresh_token=secret.get("refresh_token"),
        token_uri=secret.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=secret.get("client_id"),
        client_secret=secret.get("client_secret"),
        scopes=secret.get("scopes"),
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleAuthRequest())
        _persist_token(secret_arn, creds)
    gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
    return (
        gmail.users()
        .watch(
            userId="me",
            body={
                "topicName": topic_name,
                "labelIds": ["INBOX"],
                "labelFilterBehavior": "INCLUDE",
            },
        )
        .execute()
    )


class GmailWatchRenewalHandler(AbstractJobHandler):
    async def execute(self, job: Job, session: AsyncSession) -> None:
        if is_terminal_job(job):
            return

        claimed = await claim_job_for_worker(session, job.id, running_step=_RUNNING)
        await session.refresh(job)
        if is_terminal_job(job):
            return
        if not claimed and job.status != JobStatus.running:
            return

        settings = get_settings()
        topic = (settings.google_pubsub_topic or "").strip()
        if not topic:
            await complete_job_success(session, job.id, final_step=_DONE)
            await session.refresh(job)
            log.info(
                "gmail_watch_renewal_skipped_no_pubsub",
                job_id=str(job.id),
                message="GOOGLE_PUBSUB_TOPIC unset; push renewals disabled (polling hubs).",
            )
            return

        payload = job.payload or {}
        integ_id_raw = payload.get("integration_id")
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(days=2)

        if integ_id_raw:
            single = await session.get(Integration, UUID(str(integ_id_raw)))
            rows = [single] if single is not None else []
        else:
            rows = list(
                (
                    await session.scalars(
                        select(Integration).where(
                            Integration.provider == "gmail",
                            Integration.watch_active.is_(True),
                            Integration.watch_expires_at.is_not(None),
                            Integration.watch_expires_at < horizon,
                        )
                    )
                ).all()
            )

        if integ_id_raw and not rows:
            await fail_job_while_running(session, job.id, message="integration not found")
            return

        errors: list[str] = []
        for integ in rows:
            if integ.provider != "gmail" or not integ.watch_active:
                continue
            try:
                watch_resp = await asyncio.to_thread(
                    partial(_renew_watch_sync, secret_arn=integ.secret_arn, topic_name=topic)
                )
                exp = datetime.fromtimestamp(int(watch_resp["expiration"]) / 1000, tz=timezone.utc)
                integ.watch_expires_at = exp
                integ.last_history_id = str(watch_resp.get("historyId") or integ.last_history_id or "")
                integ.watch_resource_id = str(watch_resp.get("resourceId") or integ.watch_resource_id or "")
                integ.connection_status = "active"
            except Exception as exc:
                log.warning(
                    "gmail_watch_renew_failed",
                    integration_id=str(integ.id),
                    error=str(exc),
                )
                errors.append(str(integ.id))
                integ.connection_status = "error"

        await session.commit()

        if integ_id_raw and errors:
            await fail_job_while_running(
                session,
                job.id,
                message="gmail watch renew failed: " + ",".join(errors),
            )
            return

        await complete_job_success(session, job.id, final_step=_DONE)
        await session.refresh(job)
        log.info(
            "gmail_watch_renewal_complete",
            job_id=str(job.id),
            attempted=len(rows),
            failures=len(errors),
        )
