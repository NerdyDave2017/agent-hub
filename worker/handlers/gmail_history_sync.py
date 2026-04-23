"""Resolve Gmail ``history.list`` into per-message jobs (see gmail-pubsub-implementation.md)."""

from __future__ import annotations

import asyncio
import json
from functools import partial
from typing import Any
from uuid import UUID

import boto3
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_hub_core.config.settings import get_settings
from agent_hub_core.db.job_transitions import claim_job_for_worker, complete_job_success, fail_job_while_running
from agent_hub_core.db.models import Integration, Job
from agent_hub_core.domain.enums import JobStatus, JobType
from agent_hub_core.observability.logging import get_logger

from worker.handlers._idempotency import is_terminal_job
from worker.handlers.base import AbstractJobHandler
from worker.messaging.enqueue import enqueue_job_default_settings

log = get_logger(__name__)

_RUNNING = "gmail_history_sync_running"
_DONE = "gmail_history_sync_complete"


def _sm_get_json(secret_arn: str) -> dict[str, Any]:
    s = get_settings()
    kw: dict[str, Any] = {"region_name": s.aws_region}
    if s.aws_endpoint_url:
        kw["endpoint_url"] = s.aws_endpoint_url
    if s.aws_access_key_id and s.aws_secret_access_key:
        kw["aws_access_key_id"] = s.aws_access_key_id
        kw["aws_secret_access_key"] = s.aws_secret_access_key
    sm = boto3.client("secretsmanager", **kw)
    raw = sm.get_secret_value(SecretId=secret_arn)["SecretString"]
    return json.loads(raw)


def _credentials_from_secret(secret: dict[str, Any]) -> Credentials:
    return Credentials(
        token=secret.get("access_token"),
        refresh_token=secret.get("refresh_token"),
        token_uri=secret.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=secret.get("client_id"),
        client_secret=secret.get("client_secret"),
        scopes=secret.get("scopes"),
    )


def _persist_token(secret_arn: str, credentials: Credentials) -> None:
    s = get_settings()
    kw: dict[str, Any] = {"region_name": s.aws_region}
    if s.aws_endpoint_url:
        kw["endpoint_url"] = s.aws_endpoint_url
    if s.aws_access_key_id and s.aws_secret_access_key:
        kw["aws_access_key_id"] = s.aws_access_key_id
        kw["aws_secret_access_key"] = s.aws_secret_access_key
    sm = boto3.client("secretsmanager", **kw)
    existing = json.loads(sm.get_secret_value(SecretId=secret_arn)["SecretString"])
    existing["access_token"] = credentials.token
    sm.put_secret_value(SecretId=secret_arn, SecretString=json.dumps(existing))


def _collect_message_ids_from_history(
    *,
    credentials: Credentials,
    secret_arn: str,
    start_history_id: str | None,
) -> list[str]:
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(GoogleAuthRequest())
        _persist_token(secret_arn, credentials)
    gmail = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    message_ids: list[str] = []

    if not start_history_id:
        result = (
            gmail.users()
            .messages()
            .list(userId="me", q="is:unread in:inbox newer_than:1d", maxResults=10)
            .execute()
        )
        return [m["id"] for m in result.get("messages", []) if m.get("id")]

    page_token = None
    while True:
        kwargs: dict[str, Any] = {
            "userId": "me",
            "startHistoryId": start_history_id,
            "historyTypes": ["messageAdded"],
        }
        if page_token:
            kwargs["pageToken"] = page_token
        try:
            result = gmail.users().history().list(**kwargs).execute()
        except HttpError as exc:
            status = exc.resp.status if exc.resp else 0
            if status == 404 or "invalidhistoryid" in str(exc).lower():
                fb = (
                    gmail.users()
                    .messages()
                    .list(userId="me", q="is:unread in:inbox newer_than:1d", maxResults=10)
                    .execute()
                )
                return [m["id"] for m in fb.get("messages", []) if m.get("id")]
            raise

        for history_record in result.get("history", []):
            for msg_added in history_record.get("messagesAdded", []):
                msg = msg_added.get("message", {})
                labels = msg.get("labelIds") or []
                if "INBOX" in labels and msg.get("id"):
                    message_ids.append(str(msg["id"]))

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    return list(dict.fromkeys(message_ids))


class GmailHistorySyncHandler(AbstractJobHandler):
    async def execute(self, job: Job, session: AsyncSession) -> None:
        if is_terminal_job(job):
            return

        claimed = await claim_job_for_worker(session, job.id, running_step=_RUNNING)
        await session.refresh(job)
        if is_terminal_job(job):
            return
        if not claimed and job.status != JobStatus.running:
            log.warning(
                "gmail_history_sync_skip_status",
                job_id=str(job.id),
                job_status=job.status.value,
            )
            return

        payload = job.payload or {}
        try:
            integration_id = UUID(str(payload["integration_id"]))
        except (KeyError, ValueError, TypeError):
            await fail_job_while_running(session, job.id, message="missing integration_id")
            return

        integration = await session.get(Integration, integration_id)
        if integration is None or integration.provider != "gmail":
            await fail_job_while_running(session, job.id, message="integration not found")
            return

        start_history_id = payload.get("start_history_id")
        if isinstance(start_history_id, str) and not start_history_id.strip():
            start_history_id = None
        email_address = str(payload.get("email_address") or integration.email_address or "")

        try:
            secret = await asyncio.to_thread(_sm_get_json, integration.secret_arn)
            creds = _credentials_from_secret(secret)
            message_ids = await asyncio.to_thread(
                partial(
                    _collect_message_ids_from_history,
                    credentials=creds,
                    secret_arn=integration.secret_arn,
                    start_history_id=start_history_id,
                )
            )
        except Exception as exc:
            log.exception("gmail_history_sync_failed", job_id=str(job.id))
            await fail_job_while_running(session, job.id, message=str(exc))
            return

        parent_cid = job.correlation_id or str(job.id)
        for mid in message_ids:
            await enqueue_job_default_settings(
                session,
                tenant_id=job.tenant_id,
                agent_id=job.agent_id,
                job_type=JobType.gmail_process_message.value,
                correlation_id=parent_cid,
                idempotency_key=f"gmail_process:{job.tenant_id}:{mid}",
                payload={
                    "message_id": mid,
                    "email_address": email_address,
                    "integration_id": str(integration.id),
                    "source": "push",
                },
            )

        await complete_job_success(session, job.id, final_step=_DONE)
        await session.refresh(job)
        log.info(
            "gmail_history_sync_complete",
            job_id=str(job.id),
            message_count=len(message_ids),
        )
