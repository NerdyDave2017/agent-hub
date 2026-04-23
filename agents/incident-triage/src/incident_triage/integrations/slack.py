"""Thin Slack Web API wrapper — sync client behind ``asyncio.to_thread`` (no aiohttp)."""

from __future__ import annotations

import asyncio
from functools import partial
from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from agent_hub_core.observability.logging import get_logger

log = get_logger(__name__)


def _chat_post_message_sync(*, token: str, channel: str, text: str, blocks: list[dict[str, Any]] | None) -> str:
    client = WebClient(token=token)
    kwargs: dict[str, Any] = {"channel": channel.strip(), "text": text}
    if blocks:
        kwargs["blocks"] = blocks
    try:
        resp = client.chat_postMessage(**kwargs)
    except SlackApiError as exc:
        err = exc.response.get("error") if exc.response else str(exc)
        log.warning("slack_chat_post_failed", slack_error=err)
        raise RuntimeError(err) from exc
    if not resp.get("ok"):
        raise RuntimeError(str(resp.get("error", "slack_post_not_ok")))
    return str(resp["ts"])


async def post_message(
    *,
    token: str,
    channel: str,
    text: str,
    blocks: list[dict[str, Any]] | None = None,
) -> str:
    """Post a message; returns Slack ``ts`` for the message."""
    return await asyncio.to_thread(
        partial(_chat_post_message_sync, token=token, channel=channel, text=text, blocks=blocks)
    )
