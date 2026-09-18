"""Inbound webhook payload from the scheduling platform's booking-confirmed event."""

from datetime import date

from pydantic import BaseModel, Field

from app.core.constants import BookingVisitType, Sex
from app.schemas._validators import AwareDatetime


class BookingConfirmedWebhook(BaseModel):
    """Payload received when a patient's booking is confirmed upstream.

    Receiving this event is what creates the patient-specific AI session
    and, in turn, triggers the notification with the intake link.
    """

    appointment_id: str
    patient_name: str
    patient_id: str
    date_of_birth: date
    sex: Sex
    physician: str
    scheduled_at: AwareDatetime
    visit_type: BookingVisitType | None = Field(
        default=None,
        description=(
            "The appointment reason the patient selected when booking. Carried "
            "as a value rather than folded into `booking_reason` prose, because "
            "it is what the live call opens by confirming and what the screening "
            "is organized around -- a reason parsed back out of a sentence is a "
            "reason that can be parsed wrong. None for a platform that does not "
            "capture one."
        ),
    )
    booking_reason: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
