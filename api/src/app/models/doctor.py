"""The `doctors` domain model — a physician, their calendar, and their Google grant."""

import re
from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.core.constants import CareModality, SymptomCategory

_NON_SLUG_CHARS = re.compile(r"[^a-z0-9]+")


def slugify_doctor_name(name: str) -> str:
    """Derive a doctor's stable id from their name.

    Deterministic rather than random so re-running `seed_doctors` (which
    upserts by name) keeps the same id, and so an id already handed to the
    booking UI never changes underneath it.
    """
    return _NON_SLUG_CHARS.sub("-", name.strip().lower()).strip("-")


class DoctorGoogleGrant(BaseModel):
    """A doctor's own Google authorization, captured once at registration.

    Present only for a doctor who completed the OAuth consent flow. The
    refresh token is a long-lived credential for that doctor's calendar and
    must never leave the backend -- no API response model includes it.
    """

    email: str
    refresh_token: str
    scopes: list[str]
    granted_at: datetime


class Doctor(BaseModel):
    """One physician known to the booking platform.

    Still matched by `name` against `Session.physician` and
    `BookingConfirmedWebhook.physician` -- both free-text strings -- but the
    booking UI addresses a doctor by `doctor_id`, which is stable and safe
    to put in a URL.
    """

    name: str
    google_calendar_id: str
    timezone: str | None = Field(
        default=None,
        description=(
            "IANA zone this doctor's appointments are in. None means 'use "
            "Settings.clinic_timezone' -- existing seeded doctors need no "
            "backfill."
        ),
    )
    doctor_id: str = Field(
        default="",
        description="Slug of `name`, filled in automatically when omitted.",
    )
    credential: str | None = Field(
        default=None,
        description="Post-nominal shown beside the name on the booking card, e.g. 'MD'.",
    )
    categories: list[SymptomCategory] = Field(
        default_factory=list,
        description=(
            "Which visit types this doctor accepts. Empty means unrestricted -- "
            "an existing seeded doctor keeps appearing for every visit type "
            "rather than disappearing from booking the moment this field shipped."
        ),
    )
    modality: CareModality = CareModality.IN_PERSON
    photo_url: str | None = Field(
        default=None,
        description=(
            "Absolute URL of the doctor's portrait, shown on the booking card. "
            "Set from the doctor's own Google account picture when they "
            "register, or seeded. Absent means the card falls back to initials "
            "rather than rendering a broken image."
        ),
    )
    google_grant: DoctorGoogleGrant | None = Field(
        default=None,
        description=(
            "Set once the doctor connects their own Google account. When "
            "present, availability is read from and events are written to "
            "their calendar under their own grant rather than the shared "
            "service account."
        ),
    )

    @model_validator(mode="after")
    def _fill_doctor_id(self) -> "Doctor":
        if not self.doctor_id:
            self.doctor_id = slugify_doctor_name(self.name)
        return self
