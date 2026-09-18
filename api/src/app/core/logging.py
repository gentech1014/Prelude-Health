"""Structured logging setup.

Configures structlog to emit JSON in non-local environments and
human-readable console output locally. No module in this codebase should
call `print()`; use `structlog.get_logger(__name__)` instead.
"""

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

from app.core.config import Settings

# Defense-in-depth, not the primary fix: each of these field names is
# checked against every log call's event dict and masked if present. The
# actual leaks (e.g. notification_service.py logging a raw phone number
# and a token-bearing URL) are fixed at their call sites -- this only
# catches a future accidental repeat of the same mistake.
_REDACTED_FIELDS = frozenset(
    {
        "intake_url",
        "token",
        "contact_phone",
        "mongo_uri",
        "physician_api_key",
        "booking_webhook_secret",
        "intake_link_secret",
        "notification_provider_api_key",
        "storage_access_key",
        "storage_secret_key",
    }
)


def _redact_denylisted_fields(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Mask a small denylist of field names if a log call ever includes one."""
    for field in _REDACTED_FIELDS:
        if field in event_dict:
            event_dict[field] = "***REDACTED***"
    return event_dict


def configure_logging(settings: Settings) -> None:
    """Configure structlog + stdlib logging for the current process.

    Must be called once, at process startup, before any logger is used.
    """
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=settings.log_level.upper(),
    )

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        # Without this, `log.exception()` under the JSON renderer emits the
        # literal `"exc_info": true` and discards the traceback entirely --
        # every production stack trace silently lost.
        structlog.processors.format_exc_info,
        _redact_denylisted_fields,
    ]

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if settings.log_json
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.log_level.upper())
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
