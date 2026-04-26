"""
SQS **consumer** primitives (blocking boto3).

Mirrors ``agent_hub_core.messaging.sqs`` (hub producer + shared client factory): one place
for ``create_sqs_client`` reuse and for receive/delete call shapes. Callers run these
functions in ``asyncio.to_thread`` so the async main loop stays responsive.
"""

from __future__ import annotations

from typing import Any, TypedDict

from agent_hub_core.config.settings import Settings
from agent_hub_core.messaging.sqs import create_sqs_client


class RawSqsMessage(TypedDict, total=False):
    """Subset of fields the worker cares about from ``receive_message``."""

    MessageId: str
    ReceiptHandle: str
    Body: str


def receive_long_poll(
    *,
    settings: Settings,
    max_messages: int = 10,
    wait_seconds: int = 20,
    visibility_timeout: int = 60,
) -> list[RawSqsMessage]:
    """
    Block up to ``wait_seconds`` for up to ``max_messages`` messages.

    ``visibility_timeout`` should exceed expected handler duration so another worker
    does not see the same message mid-flight; tune per handler in later slices.
    """
    if not settings.sqs_queue_url:
        raise RuntimeError("SQS_QUEUE_URL is not set")
    client = create_sqs_client(settings)
    resp = client.receive_message(
        QueueUrl=str(settings.sqs_queue_url),
        MaxNumberOfMessages=max_messages,
        WaitTimeSeconds=wait_seconds,
        VisibilityTimeout=visibility_timeout,
    )
    raw = resp.get("Messages") or []
    return [m for m in raw if isinstance(m, dict)]


def delete_message(*, settings: Settings, receipt_handle: str) -> None:
    """Acknowledge successful processing so SQS does not redeliver."""
    if not settings.sqs_queue_url:
        raise RuntimeError("SQS_QUEUE_URL is not set")
    client: Any = create_sqs_client(settings)
    client.delete_message(QueueUrl=str(settings.sqs_queue_url), ReceiptHandle=receipt_handle)
