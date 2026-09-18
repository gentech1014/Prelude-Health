"""Shared field validators for the booking-adjacent request/webhook schemas."""

from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator


def _require_aware(value: datetime) -> datetime:
    """Reject a naive `scheduled_at` rather than guess its timezone.

    BSON already silently treats a naive datetime as if it were UTC (see
    `app.db.mongo`'s tz_aware client); interpreting naive input here as
    clinic-local instead would layer a *second* silent reinterpretation
    on top of the first, and is exactly how a wrong-by-one-offset
    appointment time would survive under a different disguise. A loud
    422 at the API edge is correct -- see `app.integrations.google_calendar`
    and `app.agents.bidi.prompts` for the render-side half of this fix.
    """
    if value.tzinfo is None:
        raise ValueError("scheduled_at must include a UTC offset (e.g. '+05:30' or 'Z')")
    return value


AwareDatetime = Annotated[datetime, AfterValidator(_require_aware)]
