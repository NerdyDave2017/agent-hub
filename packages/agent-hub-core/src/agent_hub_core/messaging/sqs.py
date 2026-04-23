"""
Thin boto3 wrapper for SQS — **SendMessage** (hub) and **client factory** (hub + worker).
"""

from __future__ import annotations

from typing import Any

import boto3

from agent_hub_core.config.settings import Settings


def create_sqs_client(settings: Settings) -> Any:
    """
    Build a regional SQS client. Passes through optional LocalStack endpoint and static
    keys only when they are set — otherwise boto3 resolves credentials the normal way.
    """
    kwargs: dict[str, Any] = {"region_name": settings.aws_region}
    if settings.aws_endpoint_url:
        kwargs["endpoint_url"] = settings.aws_endpoint_url
    if settings.aws_access_key_id and settings.aws_secret_access_key:
        kwargs["aws_access_key_id"] = settings.aws_access_key_id
        kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
    return boto3.client("sqs", **kwargs)


def send_job_envelope(*, settings: Settings, body_json: str) -> str:
    """
    Perform one `SendMessage` with a pre-serialized JSON string (the `JobQueueEnvelope`).

    Returns the SQS **MessageId** (useful for structured logs). Raises `ClientError` /
    `BotoCoreError` on transport or API failure — the caller decides how to update `jobs`.
    """
    if not settings.sqs_queue_url:
        raise RuntimeError("SQS_QUEUE_URL is not set; refusing to call SendMessage")
    client = create_sqs_client(settings)
    resp = client.send_message(QueueUrl=str(settings.sqs_queue_url), MessageBody=body_json)
    mid = resp.get("MessageId")
    if not mid:
        raise RuntimeError("SQS SendMessage returned no MessageId")
    return str(mid)
