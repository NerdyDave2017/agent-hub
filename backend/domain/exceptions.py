"""
Exceptions raised by **`services.*`** — no HTTP types here.

Routers (or `main.py` exception handlers) translate these into status codes and response bodies.
"""


class TenantNotFound(Exception):
    """No tenant row for the given id."""


class AgentNotFound(Exception):
    """No agent row for the id, or it does not belong to the tenant."""


class JobNotFound(Exception):
    """No job row for the id, or it does not belong to the tenant."""


class TenantSlugConflict(Exception):
    """Tenant slug violates a unique constraint (e.g. duplicate slug)."""

    def __init__(self) -> None:
        super().__init__("slug already exists")
