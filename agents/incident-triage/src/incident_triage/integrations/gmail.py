"""Gmail REST client: OAuth user credentials, fetch message, list unread, remove UNREAD."""

from __future__ import annotations

import asyncio
import base64
from functools import partial
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

DEFAULT_SCOPES = ("https://www.googleapis.com/auth/gmail.modify",)


def normalize_oauth_secret(data: dict[str, Any]) -> dict[str, Any]:
    """Flatten Google client JSON shapes (``installed`` / ``web``) merged with tokens."""
    if "installed" in data and isinstance(data["installed"], dict):
        inst = data["installed"]
        return {**inst, **{k: v for k, v in data.items() if k != "installed"}}
    if "web" in data and isinstance(data["web"], dict):
        web = data["web"]
        return {**web, **{k: v for k, v in data.items() if k != "web"}}
    return data


def has_usable_credentials(data: dict[str, Any] | None) -> bool:
    if not data:
        return False
    n = normalize_oauth_secret(data)
    return bool(n.get("refresh_token") and n.get("client_id") and n.get("client_secret"))


def _build_credentials(secret: dict[str, Any]) -> Credentials:
    n = normalize_oauth_secret(secret)
    scopes = n.get("scopes")
    if isinstance(scopes, str):
        scopes = [s.strip() for s in scopes.split() if s.strip()]
    if not scopes:
        scopes = list(DEFAULT_SCOPES)
    return Credentials(
        token=n.get("token"),
        refresh_token=n.get("refresh_token"),
        token_uri=n.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=n.get("client_id"),
        client_secret=n.get("client_secret"),
        scopes=scopes,
    )


def _refresh_if_needed(creds: Credentials) -> None:
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())


def _service(secret: dict[str, Any]):
    creds = _build_credentials(secret)
    _refresh_if_needed(creds)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _decode_b64url(data: str) -> str:
    pad = "=" * (-len(data) % 4)
    raw = base64.urlsafe_b64decode((data + pad).encode())
    return raw.decode("utf-8", errors="replace")


def _header_map(payload: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for h in payload.get("headers") or []:
        name = (h.get("name") or "").lower()
        if name:
            out[name] = h.get("value") or ""
    return out


def _walk_parts(part: dict[str, Any], plain: list[str], html: list[str]) -> None:
    mime = (part.get("mimeType") or "").lower()
    body = part.get("body") or {}
    data = body.get("data")
    if data and mime == "text/plain":
        plain.append(_decode_b64url(data))
    elif data and mime == "text/html":
        html.append(_decode_b64url(data))
    for sub in part.get("parts") or []:
        if isinstance(sub, dict):
            _walk_parts(sub, plain, html)


def _body_from_payload(payload: dict[str, Any]) -> str:
    plain: list[str] = []
    html: list[str] = []
    _walk_parts(payload, plain, html)
    if plain:
        return "\n".join(plain)[:120_000]
    if html:
        return html[0][:120_000]
    root_body = payload.get("body") or {}
    if root_body.get("data"):
        return _decode_b64url(root_body["data"])[:120_000]
    return ""


def fetch_message_sync(*, user_id: str, message_id: str, secret: dict[str, Any]) -> dict[str, Any]:
    """Return a ``raw_email``-shaped dict (subject, sender, body, gmail_message_id)."""
    service = _service(secret)
    msg = (
        service.users()
        .messages()
        .get(userId=user_id, id=message_id, format="full")
        .execute()
    )
    payload = msg.get("payload") or {}
    hdrs = _header_map(payload)
    return {
        "subject": hdrs.get("subject", ""),
        "sender": hdrs.get("from", ""),
        "body": _body_from_payload(payload),
        "gmail_message_id": str(msg.get("id") or message_id),
    }


def mark_as_read_sync(*, user_id: str, message_id: str, secret: dict[str, Any]) -> None:
    service = _service(secret)
    service.users().messages().modify(
        userId=user_id,
        id=message_id,
        body={"removeLabelIds": ["UNREAD"]},
    ).execute()


def list_unread_message_ids_sync(
    *,
    user_id: str,
    secret: dict[str, Any],
    max_results: int = 10,
    query: str = "is:unread newer_than:3d",
) -> list[str]:
    service = _service(secret)
    resp = (
        service.users()
        .messages()
        .list(userId=user_id, q=query, maxResults=max_results)
        .execute()
    )
    rows = resp.get("messages") or []
    return [str(m["id"]) for m in rows if isinstance(m, dict) and m.get("id")]


async def fetch_message_async(*, user_id: str, message_id: str, secret: dict[str, Any]) -> dict[str, Any]:
    return await asyncio.to_thread(
        partial(fetch_message_sync, user_id=user_id, message_id=message_id, secret=secret)
    )


async def mark_as_read_async(*, user_id: str, message_id: str, secret: dict[str, Any]) -> None:
    await asyncio.to_thread(
        partial(mark_as_read_sync, user_id=user_id, message_id=message_id, secret=secret)
    )


async def list_unread_message_ids_async(
    *,
    user_id: str,
    secret: dict[str, Any],
    max_results: int = 10,
    query: str = "is:unread newer_than:3d",
) -> list[str]:
    return await asyncio.to_thread(
        partial(
            list_unread_message_ids_sync,
            user_id=user_id,
            secret=secret,
            max_results=max_results,
            query=query,
        )
    )


def http_error_reason(exc: HttpError) -> str:
    content = getattr(exc, "content", b"") or b""
    return content.decode("utf-8", errors="replace")[:500] or str(exc)
