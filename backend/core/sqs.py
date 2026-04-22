"""
Thin boto3 wrapper for the hub’s **SQS producer** (`SendMessage`).

Why this module exists (read this once)
---------------------------------------
`apis/jobs.py` owns HTTP and **Postgres transaction boundaries**. Dropping raw boto3 calls
there would bury endpoint wiring, credential rules, and error types. This file answers
only: “given `Settings`, how do we build a client and send one JSON body?”

Local vs production
--------------------
* **LocalStack:** set `AWS_ENDPOINT_URL` and usually explicit dummy keys in `.env` (see README).
* **AWS:** leave `AWS_ENDPOINT_URL` unset; omit static keys so boto3 follows the **default
  credential chain** (ECS task role, env vars in CI, etc.).

Blocking calls and async routes
--------------------------------
`send_job_envelope` is **synchronous** (boto3). The jobs router runs it inside
`asyncio.to_thread` so the event loop is not blocked while the hub waits on SQS.
"""

from __future__ import annotations

from typing import Any

import boto3

from core.settings import Settings


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
