"""Resolves which IANA zone one doctor's appointments render in.

Two layers, resolved once (at booking) and stamped onto
`Session.appointment_timezone` -- never re-looked-up at render time, so a
later change to either layer cannot retroactively re-render an
already-booked session in a different zone. See that field's docstring.
"""

from app.core.config import Settings
from app.models.doctor import Doctor


def resolve_appointment_timezone(doctor: Doctor | None, settings: Settings) -> str:
    """`Doctor.timezone` overrides `Settings.clinic_timezone` when set."""
    if doctor is not None and doctor.timezone:
        return doctor.timezone
    return settings.clinic_timezone
