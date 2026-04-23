"""
Exceptions raised by **`services.*`** — no HTTP types here.

Each exception carries **structured fields** (`error_code`, `status_code`, optional ids) so
`main.py` can render consistent JSON without string-matching ``Exception.args``.

Routers should **not** construct these with bare ``raise X``; always pass the identifiers
that explain *which* row failed (see ``tenants_service`` / ``agents_service`` / ``jobs_service``).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID


class DomainError(Exception):
    """
    Base for all hub domain failures.

    ``message`` is human-readable; ``error_code`` is stable for clients and logs;
    ``status_code`` is the intended HTTP status when mapped in FastAPI.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        error_code: str,
        **context: Any,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.context = context

    def as_log_dict(self) -> dict[str, Any]:
        """Structured fields for structlog / CloudWatch."""
        return {
            "error_code": self.error_code,
            "status_code": self.status_code,
            **self.context,
        }


class TenantNotFound(DomainError):
    """No tenant row for the given id."""

    def __init__(self, tenant_id: UUID) -> None:
        self.tenant_id = tenant_id
        super().__init__(
            f"Tenant {tenant_id} not found",
            status_code=404,
            error_code="TENANT_NOT_FOUND",
            tenant_id=str(tenant_id),
        )


class AgentNotFound(DomainError):
    """No agent row for the id, or it does not belong to the tenant."""

    def __init__(self, agent_id: UUID, tenant_id: UUID) -> None:
        self.agent_id = agent_id
        self.tenant_id = tenant_id
        super().__init__(
            f"Agent {agent_id} not found or does not belong to tenant {tenant_id}",
            status_code=404,
            error_code="AGENT_NOT_FOUND",
            agent_id=str(agent_id),
            tenant_id=str(tenant_id),
        )


class JobNotFound(DomainError):
    """No job row for the id, or it does not belong to the tenant."""

    def __init__(self, job_id: UUID, tenant_id: UUID) -> None:
        self.job_id = job_id
        self.tenant_id = tenant_id
        super().__init__(
            f"Job {job_id} not found or does not belong to tenant {tenant_id}",
            status_code=404,
            error_code="JOB_NOT_FOUND",
            job_id=str(job_id),
            tenant_id=str(tenant_id),
        )


class TenantSlugConflict(DomainError):
    """Tenant slug violates a unique constraint (e.g. duplicate slug)."""

    def __init__(self, slug: str) -> None:
        self.slug = slug
        super().__init__(
            f"Tenant slug {slug!r} already exists",
            status_code=409,
            error_code="TENANT_SLUG_CONFLICT",
            slug=slug,
        )
