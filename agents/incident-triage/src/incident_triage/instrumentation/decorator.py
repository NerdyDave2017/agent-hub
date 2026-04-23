"""Wraps graph node callables: times execution, re-raises errors, fires async ``write_tool_event``."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, TypeVar

from incident_triage.instrumentation.events import write_tool_event

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def traced_node(node_name: str) -> Callable[[F], F]:
    def decorator(fn: F) -> F:
        @wraps(fn)
        async def wrapper(state: Any, **kwargs: Any) -> Any:
            start = time.monotonic()
            err: str | None = None
            out: dict[str, Any] = {}
            try:
                raw = await fn(state, **kwargs)
                out = raw if isinstance(raw, dict) else {}
                return raw
            except Exception as exc:
                err = str(exc)
                raise
            finally:
                duration_ms = int((time.monotonic() - start) * 1000)
                asyncio.create_task(
                    write_tool_event(
                        tenant_id=getattr(state, "tenant_id", "") or "",
                        agent_id=getattr(state, "agent_id", "") or "",
                        trace_id=getattr(state, "langfuse_trace_id", "") or "",
                        message_id=getattr(state, "message_id", "") or "",
                        node_name=node_name,
                        tool_name=out.get("_tool_name"),
                        decision=out.get("_decision"),
                        duration_ms=duration_ms,
                        succeeded=err is None,
                        error=err,
                        prompt_tokens=out.get("_prompt_tokens"),
                        completion_tokens=out.get("_completion_tokens"),
                    )
                )

        return wrapper  # type: ignore[return-value]

    return decorator
