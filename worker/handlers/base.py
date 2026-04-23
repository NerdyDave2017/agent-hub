"""Abstract job handler — keep orchestration out of ``worker.main``."""

from __future__ import annotations

from abc import ABC, abstractmethod

from sqlalchemy.ext.asyncio import AsyncSession

from agent_hub_core.db.models import Job


class AbstractJobHandler(ABC):
    """Implement ``execute`` for one ``JobType``; commit policy is per-handler."""

    @abstractmethod
    async def execute(self, job: Job, session: AsyncSession) -> None:
        """Mutate ``job`` / related rows and **commit** as needed."""
