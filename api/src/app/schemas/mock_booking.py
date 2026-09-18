"""API schemas for the mock scheduling-platform endpoint."""

from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator

from app.core.constants import BookingVisitType, Sex, SymptomCategory
from app.schemas._validators import AwareDatetime


class ScheduleAppointmentRequest(BaseModel):
    """What a future Booking UI's "Schedule" action would submit.

    Deliberately the same shape as `BookingConfirmedWebhook` minus
    `appointment_id` -- this mock is what *generates* that id, standing in
    for the real platform's own booking flow.
    """

    patient_name: str
    patient_id: str | None = Field(
        default=None,
        description=(
            "The clinic's own identifier for the patient, when they know it. "
            "Optional on purpose -- most patients cannot recall an MRN, and "
            "blocking a booking on one is worse than issuing a provisional id "
            "the clinic reconciles later. Omitted, the server mints one."
        ),
    )
    date_of_birth: date
    sex: Sex
    physician: str = Field(
        default="",
        description=(
            "Doctor's display name. Optional when `doctor_id` is given -- the "
            "booking UI addresses a doctor by id, and the name is then read "
            "from their record rather than trusted from the client."
        ),
    )
    doctor_id: str | None = Field(
        default=None,
        description="Stable doctor id from `GET /booking/providers`. Preferred over `physician`.",
    )
    visit_type: BookingVisitType | None = Field(
        default=None,
        description=(
            "What the patient picked on the booking screen. Preferred over "
            "`symptom_category`: it also covers the unscoped options (general "
            "checkup, not sure), and the server derives the clinical category "
            "from it rather than trusting the client to map it."
        ),
    )
    symptom_category: SymptomCategory | None = Field(
        default=None,
        description=(
            "The clinical category to pre-scope the call to. Ignored when "
            "`visit_type` is given. Pre-scoping only; never treated as a "
            "diagnosis, and never a substitute for what the call establishes."
        ),
    )
    scheduled_at: AwareDatetime
    booking_reason: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None

    @model_validator(mode="after")
    def _require_a_doctor_reference(self) -> "ScheduleAppointmentRequest":
        """One of `doctor_id` or `physician` must identify the doctor."""
        if not self.doctor_id and not self.physician:
            raise ValueError("Either doctor_id or physician is required")
        return self


class ScheduleAppointmentResponse(BaseModel):
    """Returned to the Booking UI once the mock scheduling platform has run."""

    session_id: str
    appointment_id: str
    intake_url: str
    calendar_event_id: str
    physician: str = Field(
        default="",
        description="Resolved doctor name, so the confirmation screen need not re-fetch it.",
    )
    scheduled_at: datetime | None = Field(
        default=None, description="The booked start time, as stored."
    )
    patient_id: str = Field(
        default="",
        description=(
            "The identifier the session was created under -- the submitted one, "
            "or the provisional id minted for it."
        ),
    )
    patient_id_is_provisional: bool = Field(
        default=False,
        description=(
            "True when the server minted the identifier because none was given. "
            "Surfaced so the booking UI can say so rather than presenting a "
            "generated id as though the clinic had issued it."
        ),
    )
    notification_message: str = Field(
        default="",
        description=(
            "The exact SMS body sent to the patient, intake link included. "
            "Returned so the booking screen can show what the patient receives "
            "instead of re-composing an approximation of it."
        ),
    )
    calendar_synced: bool = Field(
        default=False,
        description=(
            "Whether the appointment actually landed on a Google Calendar. "
            "False means the booking is recorded here but the doctor's "
            "calendar was not updated -- the UI says so rather than implying "
            "a sync that did not happen."
        ),
    )
