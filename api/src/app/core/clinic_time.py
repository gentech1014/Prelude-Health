"""Resolving the clinic's wall-clock timezone.

Shared by `AvailabilityService` (which generates bookable slots in it) and
`SessionService` (which speaks an appointment time back to the patient in
the intake message). Both must agree, or a slot offered as 9:00 AM arrives
in the patient's SMS as something else.
"""

from datetime import UTC, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog

from app.core.config import Settings

log = structlog.get_logger(__name__)


def resolve_clinic_zone(settings: Settings) -> tzinfo:
    """The clinic's timezone, falling back to fixed UTC on a bad configuration.

    A typo in `CLINIC_TIMEZONE` must not take booking down; a warning plus
    UTC is a far better failure than a 500 on every page load.

    The fallback is `datetime.UTC`, not `ZoneInfo("UTC")`: on a host with no
    IANA database (Windows without `tzdata`) even the UTC key raises, so a
    `ZoneInfo` fallback would re-raise the very error it exists to absorb.
    `tzdata` is a declared dependency so configured zones do resolve, but
    the fallback must not depend on it.
    """
    try:
        return ZoneInfo(settings.clinic_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("clinic_timezone_invalid", value=settings.clinic_timezone)
        return UTC
