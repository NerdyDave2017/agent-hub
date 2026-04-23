"""Map ``JobType`` → handler class — add one line per new async job kind."""

from __future__ import annotations

from agent_hub_core.domain.enums import JobType

from worker.handlers.base import AbstractJobHandler
from worker.handlers.destroy import AgentDestroyHandler
from worker.handlers.gmail_history_sync import GmailHistorySyncHandler
from worker.handlers.gmail_process_message import GmailProcessMessageHandler
from worker.handlers.gmail_watch_renewal import GmailWatchRenewalHandler
from worker.handlers.integration_rotate import IntegrationRotateHandler
from worker.handlers.metrics_rollup import MetricsRollupHandler
from worker.handlers.pause import AgentPauseHandler
from worker.handlers.provision import AgentProvisioningHandler

JOB_HANDLER_REGISTRY: dict[JobType, type[AbstractJobHandler]] = {
    JobType.agent_provisioning: AgentProvisioningHandler,
    JobType.agent_pause: AgentPauseHandler,
    JobType.deployment_scale_to_zero: AgentPauseHandler,
    JobType.agent_deprovision: AgentDestroyHandler,
    JobType.agent_destroy: AgentDestroyHandler,
    JobType.integration_rotate: IntegrationRotateHandler,
    JobType.metrics_rollup: MetricsRollupHandler,
    JobType.gmail_history_sync: GmailHistorySyncHandler,
    JobType.gmail_process_message: GmailProcessMessageHandler,
    JobType.gmail_watch_renewal: GmailWatchRenewalHandler,
}


def handler_for_job_type(job_type: str) -> type[AbstractJobHandler] | None:
    """Resolve string ``jobs.job_type`` (DB + envelope) to a handler class."""
    try:
        jt = JobType(job_type)
    except ValueError:
        return None
    return JOB_HANDLER_REGISTRY.get(jt)
