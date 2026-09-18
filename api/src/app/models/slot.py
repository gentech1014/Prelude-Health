"""The `appointment_slots` domain model — one bookable half-hour, per doctor.

Source of truth for availability. Google Calendar is a delivery mirror,
never consulted for availability (`GoogleCalendarClient` has no free/busy
method, and adding one would put a third-party round trip on the
dead-air path of a live voice call).
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.core.constants import SlotStatus


class AppointmentSlot(BaseModel):
    """One bookable slot for one physician.

    `session_id` names whoever currently holds this slot, in whichever
    status -- `status` alone disambiguates a soft hold from a firm
    booking, which keeps every guarded write a single
    `(slot_id, expected_status, session_id)` triple rather than two
    separate reference fields to keep in sync.
    """

    slot_id: str
    physician: str = Field(description="Same free-text join key Session.physician already uses")
    starts_at: datetime = Field(description="UTC-aware")
    ends_at: datetime = Field(description="UTC-aware, stored rather than derived from a duration")
    status: SlotStatus = SlotStatus.FREE
    session_id: str | None = None
    held_until: datetime | None = Field(
        default=None, description="Only meaningful when status == HELD"
    )
    version: int = Field(
        default=0,
        description=(
            "Incremented on every claim/release. Both the CAS token for this "
            "slot and the linkage key into `scheduling_events.version_after`, "
            "which is how a lost audit write is detected after the fact."
        ),
    )
    updated_at: datetime


class SlotOffer(BaseModel):
    """One earlier slot as handed to the live agent -- never a raw
    timestamp. `spoken_time` is pre-formatted in the clinic's local zone
    (e.g. "Tuesday at 10 in the morning") specifically so Nova Sonic has
    no reason to perform its own timezone arithmetic; see
    `app.services.scheduling_service` for the formatter."""

    slot_id: str
    spoken_time: str
