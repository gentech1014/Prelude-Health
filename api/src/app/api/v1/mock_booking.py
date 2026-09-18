"""Mock scheduling platform: stands in for the real one until a Booking UI exists.

The BA scope requires only a simulated booking platform. This endpoint is
that simulation's server side -- it is what a future Booking UI's
"Schedule" action would call. It owns exactly what a real scheduling
platform would own: creating the appointment (here, a Google Calendar
event), creating the underlying bookable slot, and triggering the
downstream AI orchestration flow. It calls `SessionService.create_from_booking`
directly rather than round-tripping through `POST /webhooks/booking-confirmed`
over HTTP -- this mock owns both sides of that contract, so the extra
network hop would add latency and failure modes without adding safety;
the real webhook route stays fully implemented and independently secured
for whenever an actual scheduling platform replaces this mock (see
`app.integrations.booking_platform`'s swap-seam docstring).
"""

import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import (
    SettingsDep,
    get_availability_service,
    get_doctor_calendar_client,
    get_doctor_repository,
    get_google_calendar_client,
    get_session_repository,
    get_session_service,
    get_slot_repository,
)
from app.core.constants import BOOKING_VISIT_TYPES, BookingVisitType
from app.core.exceptions import AppointmentTimeUnavailableError, DoctorNotFoundError
from app.core.timezones import resolve_appointment_timezone
from app.integrations.doctor_calendar import DoctorCalendarClient
from app.integrations.google_calendar import GoogleCalendarClient
from app.repositories.doctor_repository import DoctorRepository
from app.repositories.session_repository import SessionRepository
from app.repositories.slot_repository import SlotRepository
from app.schemas.booking import BookingConfirmedWebhook
from app.schemas.mock_booking import ScheduleAppointmentRequest, ScheduleAppointmentResponse
from app.services.availability_service import AvailabilityService
from app.services.session_service import SessionService

router = APIRouter(prefix="/mock-booking", tags=["mock-booking"])


@router.post("/appointments", response_model=ScheduleAppointmentResponse, status_code=201)
async def schedule_appointment(
    payload: ScheduleAppointmentRequest,
    settings: SettingsDep,
    doctor_repository: Annotated[DoctorRepository, Depends(get_doctor_repository)],
    calendar_client: Annotated[GoogleCalendarClient, Depends(get_google_calendar_client)],
    doctor_calendar: Annotated[DoctorCalendarClient, Depends(get_doctor_calendar_client)],
    availability_service: Annotated[AvailabilityService, Depends(get_availability_service)],
    session_service: Annotated[SessionService, Depends(get_session_service)],
    session_repository: Annotated[SessionRepository, Depends(get_session_repository)],
    slot_repository: Annotated[SlotRepository, Depends(get_slot_repository)],
) -> ScheduleAppointmentResponse:
    """Book an appointment: verify the slot, create the calendar event, then the AI session.

    Raises 404 (`DoctorNotFoundError`) when neither `doctor_id` nor
    `physician` matches a doctor on file, and 409
    (`AppointmentTimeUnavailableError`) when the requested time is no longer open --
    the patient may have had the page open while someone else took it.

    The event is written under the doctor's own Google grant when they have
    registered one, and falls back to the shared service account otherwise.
    """
    doctor = (
        await doctor_repository.get_by_id(payload.doctor_id)
        if payload.doctor_id
        else await doctor_repository.get_by_name(payload.physician)
    )
    if doctor is None:
        raise DoctorNotFoundError(payload.doctor_id or payload.physician)

    # Re-checked server-side: the client's slot list is a snapshot, and
    # trusting it is what lets two patients hold the same appointment.
    if not await availability_service.is_slot_bookable(doctor, payload.scheduled_at):
        raise AppointmentTimeUnavailableError(doctor.name)

    appointment_timezone = resolve_appointment_timezone(doctor, settings)
    duration = timedelta(minutes=settings.slot_duration_minutes)

    summary = f"Pre-screening appointment: {payload.patient_name}"
    # The calendar entry keeps both halves as prose, unlike the session,
    # which keeps the selection as a value -- a doctor reading their own
    # calendar wants the words, and nothing parses this back out.
    visit_type = _booked_visit_type(payload)
    description = " | ".join(
        part
        for part in (
            BOOKING_VISIT_TYPES[visit_type].label if visit_type else None,
            _booking_reason(payload),
        )
        if part
    )
    if doctor_calendar.can_serve(doctor):
        event_id = await doctor_calendar.create_event(
            doctor, summary=summary, start=payload.scheduled_at, description=description
        )
    else:
        event_id = await calendar_client.create_event(
            doctor.google_calendar_id,
            summary=summary,
            start=payload.scheduled_at,
            tz=appointment_timezone,
            description=description,
            duration=duration,
        )

    appointment_id = uuid.uuid4().hex
    patient_id = payload.patient_id.strip() if payload.patient_id else ""
    is_provisional = not patient_id
    if is_provisional:
        patient_id = _provisional_patient_id()

    webhook = BookingConfirmedWebhook(
        appointment_id=appointment_id,
        patient_name=payload.patient_name,
        patient_id=patient_id,
        date_of_birth=payload.date_of_birth,
        sex=payload.sex,
        physician=doctor.name,
        scheduled_at=payload.scheduled_at,
        visit_type=_booked_visit_type(payload),
        booking_reason=_booking_reason(payload),
        contact_phone=payload.contact_phone,
        contact_email=payload.contact_email,
    )
    session = await session_service.create_from_booking(webhook)
    await session_repository.set_calendar_event_id(session.session_id, event_id)

    # find_or_create_booked is the backward-compatibility seam for this
    # free-form scheduled_at: it books whatever slot already exists at
    # this exact (physician, time) -- e.g. pre-seeded by
    # scripts/seed_slots.py -- or creates one already BOOKED if nothing
    # was seeded there. Either way the session ends up holding a real
    # slot it can later cancel or reschedule out of.
    # `doctor.name`, never `payload.physician`: the booking UI addresses a
    # doctor by `doctor_id` and leaves `physician` empty, and the slot's
    # physician is the join key `SchedulingService` matches against
    # `Session.physician` (itself `doctor.name`). An empty one here books a
    # slot no reschedule can ever find.
    slot = await slot_repository.find_or_create_booked(
        doctor.name,
        payload.scheduled_at,
        payload.scheduled_at + duration,
        session.session_id,
    )
    await session_repository.set_slot_id(session.session_id, slot.slot_id)

    return ScheduleAppointmentResponse(
        session_id=session.session_id,
        appointment_id=appointment_id,
        intake_url=session_service.build_intake_url(session.session_id),
        calendar_event_id=event_id,
        physician=doctor.name,
        scheduled_at=payload.scheduled_at,
        patient_id=patient_id,
        patient_id_is_provisional=is_provisional,
        notification_message=session_service.build_intake_message(session),
        calendar_synced=bool(event_id),
    )


def _provisional_patient_id() -> str:
    """Mint an identifier for a patient who did not supply one.

    Prefixed, not opaque: `PatientRef.patient_id` is a required key that the
    session, report and calendar delivery all hang off, so it cannot simply
    be blank -- but a bare random string would be indistinguishable from a
    clinic-issued MRN to whoever reconciles the record later.
    """
    return f"provisional-{uuid.uuid4().hex[:10]}"


def _booked_visit_type(payload: ScheduleAppointmentRequest) -> BookingVisitType | None:
    """The appointment reason the patient selected, as a value.

    This used to be flattened into `booking_reason` as the sentence
    "Patient selected visit type: General checkup", and the selection
    itself was then thrown away. The live call had to recover it by reading
    that sentence, which it was explicitly told to distrust -- so in
    practice it did not recover it at all, and every booking that carried
    no clinical category arrived at the screening with nothing to go on.

    `symptom_category` is accepted as the older, narrower way of saying the
    same thing: its five values are string-identical to their visit-type
    counterparts, so an integration still sending it keeps working.
    """
    if payload.visit_type is not None:
        return payload.visit_type
    if payload.symptom_category is not None:
        return BookingVisitType(payload.symptom_category.value)
    return None


def _booking_reason(payload: ScheduleAppointmentRequest) -> str | None:
    """The free-text reason the patient typed, if any.

    Just their words now. What they *selected* travels as
    `_booked_visit_type`, where the call can act on it.
    """
    reason = (payload.booking_reason or "").strip()
    return reason or None
