"""API schemas for the booking UI: visit types, providers, availability, registration.

Separate from `app.schemas.booking` (the inbound webhook contract) and
`app.schemas.mock_booking` (the submit contract) because this is the read
side -- what the booking screen needs to render itself from live data
rather than from constants compiled into the frontend.

None of these response models carry a doctor's refresh token or any other
credential; only the fact that a Google connection exists.
"""

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.core.constants import (
    AvailabilitySource,
    BookingVisitType,
    CareModality,
    SymptomCategory,
)
from app.models.doctor import Doctor
from app.services.availability_service import AvailableDay


class VisitTypeOption(BaseModel):
    """One selectable visit type, with the plain-language copy to render."""

    visit_type: BookingVisitType
    label: str
    description: str
    symptom_category: SymptomCategory | None = Field(
        description=(
            "The clinical category this visit type pre-scopes the call to, or "
            "null for the unscoped options (general checkup, not sure). Null "
            "is not a gap: the agent infers the category from the patient's "
            "own words during the call either way."
        )
    )


class ProviderOption(BaseModel):
    """One bookable provider, as the provider picker needs them.

    `calendar_connected` is deliberately exposed: a provider whose own
    calendar is being checked offers stronger availability than one whose
    slots come from clinic hours alone, and the UI says so rather than
    presenting both identically.
    """

    doctor_id: str
    name: str
    credential: str | None
    categories: list[SymptomCategory]
    modality: CareModality
    calendar_connected: bool
    photo_url: str | None

    @classmethod
    def from_doctor(cls, doctor: Doctor) -> "ProviderOption":
        """Project a `Doctor` down to what the booking UI may see."""
        return cls(
            doctor_id=doctor.doctor_id,
            name=doctor.name,
            credential=doctor.credential,
            categories=doctor.categories,
            modality=doctor.modality,
            calendar_connected=doctor.google_grant is not None,
            photo_url=doctor.photo_url,
        )


class SlotOption(BaseModel):
    """One bookable start time, as an offset-aware timestamp.

    `start` is what the submit request must echo back verbatim; `label` is
    the clinic-local rendering, computed server-side so every client shows
    the same time for a slot regardless of the browser's own timezone.
    """

    start: datetime
    end: datetime
    label: str


class AvailabilityDay(BaseModel):
    """One date on the booking strip."""

    day: date
    weekday_label: str
    day_label: str
    source: AvailabilitySource
    slots: list[SlotOption]


class ProviderAvailabilityResponse(BaseModel):
    """Everything the date-and-time step renders for one provider."""

    doctor_id: str
    timezone: str
    calendar_connected: bool
    days: list[AvailabilityDay]

    @classmethod
    def build(
        cls, doctor: Doctor, timezone: str, days: list[AvailableDay]
    ) -> "ProviderAvailabilityResponse":
        """Format computed availability for the wire, in the clinic's own timezone."""
        return cls(
            doctor_id=doctor.doctor_id,
            timezone=timezone,
            calendar_connected=doctor.google_grant is not None,
            days=[
                AvailabilityDay(
                    day=available.day,
                    weekday_label=available.day.strftime("%a"),
                    day_label=str(available.day.day),
                    source=available.source,
                    slots=[
                        SlotOption(
                            start=slot.start,
                            end=slot.end,
                            label=slot.start.strftime("%I:%M %p").lstrip("0"),
                        )
                        for slot in available.slots
                    ],
                )
                for available in days
            ],
        )


class DoctorRegistrationRequest(BaseModel):
    """What the "Register as doctor" dialog submits before the Google redirect."""

    name: str = Field(min_length=2, max_length=120)
    credential: str | None = Field(default=None, max_length=24)
    categories: list[SymptomCategory] = Field(default_factory=list)
    modality: CareModality = CareModality.IN_PERSON_AND_VIRTUAL


class DoctorRegistrationStartResponse(BaseModel):
    """The consent URL the browser must navigate to next."""

    authorization_url: str
