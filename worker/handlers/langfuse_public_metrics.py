"""Optional Langfuse Public Metrics API enrichment (v1 ``/api/public/metrics``)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx

from agent_hub_core.config.settings import Settings
from agent_hub_core.observability.logging import get_logger

log = get_logger(__name__)


def iso_z(dt: datetime) -> str:
    """UTC ISO-8601 with ``Z`` suffix (Langfuse examples)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    s = dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    return s.replace("+00:00", "Z")


def _num(v: Any) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v))
    except ValueError:
        return 0.0


def _parse_observations_row(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize first Langfuse metrics row (observations view)."""
    return {
        "observation_count": int(_num(row.get("count_count"))),
        "total_tokens": _num(row.get("totalTokens_sum")),
        "total_cost_usd": _num(row.get("totalCost_sum")),
        "latency_avg_ms": _num(row.get("latency_avg")),
        "data_source": "langfuse_public_metrics_v1",
        "view": "observations",
    }


def _parse_traces_row(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize first Langfuse metrics row (traces view — fallback)."""
    return {
        "trace_count": int(_num(row.get("count_count"))),
        "total_tokens": _num(row.get("totalTokens_sum")),
        "total_cost_usd": _num(row.get("totalCost_sum")),
        "latency_avg_ms": _num(row.get("latency_avg")),
        "data_source": "langfuse_public_metrics_v1",
        "view": "traces",
    }


async def fetch_langfuse_observation_totals(
    settings: Settings,
    *,
    tenant_id: UUID,
    window_start: datetime,
    window_end: datetime,
) -> dict[str, Any] | None:
    """
    Aggregate observation counts/tokens/cost/latency for traces tagged with ``userId`` = tenant UUID.

    Expects the agent to set Langfuse user id to the hub tenant id (see incident-triage metadata).
    Returns ``None`` if API keys are unset, request fails, or response is empty.
    """
    pk = (settings.langfuse_public_key or "").strip()
    sk = (settings.langfuse_secret_key or "").strip()
    if not pk or not sk:
        return None

    base = (settings.langfuse_host or "https://cloud.langfuse.com").rstrip("/")
    filters: list[dict[str, Any]] = [
        {
            "column": "userId",
            "operator": "=",
            "value": str(tenant_id),
            "type": "string",
        }
    ]
    time_from = iso_z(window_start)
    time_to = iso_z(window_end)
    metric_specs: list[dict[str, str]] = [
        {"measure": "count", "aggregation": "count"},
        {"measure": "totalTokens", "aggregation": "sum"},
        {"measure": "totalCost", "aggregation": "sum"},
        {"measure": "latency", "aggregation": "avg"},
    ]

    url = f"{base}/api/public/metrics"
    body: dict[str, Any] = {}
    chosen_parser = _parse_observations_row
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            for view, parser in (
                ("observations", _parse_observations_row),
                ("traces", _parse_traces_row),
            ):
                query_obj: dict[str, Any] = {
                    "view": view,
                    "metrics": metric_specs,
                    "dimensions": [],
                    "filters": filters,
                    "fromTimestamp": time_from,
                    "toTimestamp": time_to,
                }
                r = await client.get(
                    url,
                    params={"query": json.dumps(query_obj, separators=(",", ":"))},
                    auth=httpx.BasicAuth(pk, sk),
                    headers={"Accept": "application/json"},
                )
                if r.status_code in (400, 422) and view == "observations":
                    log.warning(
                        "langfuse_metrics_observations_rejected_trying_traces",
                        status_code=r.status_code,
                        snippet=r.text[:300],
                    )
                    continue
                if r.status_code >= 400:
                    log.warning(
                        "langfuse_metrics_http_error",
                        view=view,
                        status_code=r.status_code,
                        snippet=r.text[:400],
                    )
                    return None
                body = r.json()
                chosen_parser = parser
                break
            else:
                return None
    except httpx.HTTPError:
        log.exception("langfuse_metrics_http_exception")
        return None
    except json.JSONDecodeError:
        log.exception("langfuse_metrics_invalid_json")
        return None

    data = body.get("data")
    if not isinstance(data, list) or len(data) == 0:
        out = chosen_parser({})
        out["note"] = "empty_data"
        return out

    first = data[0]
    if not isinstance(first, dict):
        out = chosen_parser({})
        out["note"] = "unexpected_row_shape"
        return out
    return chosen_parser(first)
