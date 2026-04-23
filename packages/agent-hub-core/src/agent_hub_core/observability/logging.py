"""
Production-oriented **structlog** → **JSON** on stdout.

Includes **UTC ISO timestamps**, **log level**, **logger name**, default **service** field, and
**callsite** metadata: ``pathname``, ``filename``, ``lineno``, ``func_name`` (via
``CallsiteParameterAdder``).

Hub: ``configure_logging("hub", attach_to_root=True)`` once at startup (clears existing root
handlers so one JSON formatter owns the process). Worker: ``configure_logging("worker")`` —
same processors; pass ``attach_to_root=False`` if you embed the worker in a process that
already configured logging.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.processors import CallsiteParameterAdder
from structlog.stdlib import BoundLogger, LoggerFactory, ProcessorFormatter

def _bind_default_service(service: str):
    def _processor(
        logger: logging.Logger,
        method_name: str,
        event_dict: dict[str, Any],
    ) -> dict[str, Any]:
        event_dict.setdefault("service", service)
        return event_dict

    return _processor


def _build_shared_processors(service: str) -> list[Any]:
    return [
        _bind_default_service(service),
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
        CallsiteParameterAdder(
            parameters=[
                structlog.processors.CallsiteParameter.PATHNAME,
                structlog.processors.CallsiteParameter.FILENAME,
                structlog.processors.CallsiteParameter.LINENO,
                structlog.processors.CallsiteParameter.FUNC_NAME,
            ],
        ),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]


def configure_logging(
    service: str,
    *,
    attach_to_root: bool = False,
    level: int = logging.INFO,
) -> None:
    """
    Configure structlog + a stdlib ``StreamHandler`` on the root logger using
    ``ProcessorFormatter`` so both ``structlog.get_logger()`` and legacy ``logging.getLogger()``
    emit the same JSON shape when they use compatible kwargs.
    """
    shared = _build_shared_processors(service)

    structlog.configure(
        processors=shared
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=LoggerFactory(),
        wrapper_class=BoundLogger,
        cache_logger_on_first_use=True,
    )

    def _event_to_message(_logger: logging.Logger, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        if "event" in event_dict:
            event_dict["message"] = event_dict.pop("event")
        return event_dict

    formatter = ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            ProcessorFormatter.remove_processors_meta,
            _event_to_message,
            structlog.processors.JSONRenderer(),
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.setLevel(level)

    root = logging.getLogger()
    if attach_to_root:
        root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def get_logger(*args: Any, **kwargs: Any) -> BoundLogger:
    """Preferred logger for hub, worker, and handlers — JSON with callsite after configure_logging."""
    return structlog.get_logger(*args, **kwargs)
