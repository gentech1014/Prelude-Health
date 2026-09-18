"""API schemas for the patient's own appointment changes."""

from datetime import datetime

from pydantic import BaseModel, Field


class RescheduleAppointmentRequest(BaseModel):
    """A new start time, echoed back from an availability response.

    Deliberately just the start: the length comes from the clinic's
    configured slot size, and letting a client name its own end time would
    let it book a two-hour appointment in a thirty-minute slot.
    """

    start: datetime = Field(
        description=(
            "The chosen slot's `start`, verbatim from the availability response. "
            "Offset-aware -- a naive value is ambiguous about which hour the "
            "patient actually picked."
        )
    )
