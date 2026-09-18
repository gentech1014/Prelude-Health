"""The patient's own view of their appointment: open times, and moving it.

Kept apart from `app.api.v1.booking`, which is the *booking UI's* read
side and is not behind a patient session. These two routes are behind the
patient's `HttpOnly` session cookie and are scoped to one session's own
doctor, so the patient app never has to resolve a `doctor_id` itself or
reach an endpoint that can enumerate providers.

Both are thin: the work is `AppointmentService`, shared with the live
agent's own reschedule tools so a time offered on screen and a time
offered out loud can never be two different lists.

Also kept in its own module rather than added to `app.api.v1.sessions`,
which is already the busiest file in the API and the one most likely to
conflict on a merge.
"""

from datetime import date
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query

from app.api.deps import (
    SettingsDep,
    get_appointment_service,
    require_session_cookie,
)
from app.schemas.appointment import RescheduleAppointmentRequest
from app.schemas.booking_ui import ProviderAvailabilityResponse
from app.schemas.session import SessionContextResponse
from app.services.appointment_service import AppointmentService

log = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/sessions/{session_id}/appointment",
    tags=["appointment"],
    dependencies=[Depends(require_session_cookie)],
)


@router.get("/availability", response_model=ProviderAvailabilityResponse)
async def get_appointment_availability(
    session_id: str,
    appointments: Annotated[AppointmentService, Depends(get_appointment_service)],
    from_day: Annotated[
        date | None, Query(alias="from", description="First date to return. Defaults to today.")
    ] = None,
    days: Annotated[int | None, Query(ge=1, le=90)] = None,
) -> ProviderAvailabilityResponse:
    """Open times with this session's own doctor.

    Raises 404 when the session is unknown, or when its `physician` string
    matches no doctor on file -- sessions name their physician as free
    text, so a typo at booking time genuinely leaves nothing to look up.
    """
    doctor, available_days = await appointments.open_days(session_id, from_day=from_day, days=days)
    return ProviderAvailabilityResponse.build(
        doctor, timezone=str(appointments.clinic_timezone), days=available_days
    )


@router.post("/reschedule", response_model=SessionContextResponse)
async def reschedule_appointment(
    session_id: str,
    payload: RescheduleAppointmentRequest,
    settings: SettingsDep,
    appointments: Annotated[AppointmentService, Depends(get_appointment_service)],
) -> SessionContextResponse:
    """Move this session's appointment to a new start time.

    The slot is re-checked server-side rather than trusted from the
    request: the patient's list of open times is a snapshot, and someone
    else may have taken the slot while they were reading it. That returns
    409, and the patient app re-fetches rather than retrying the same
    payload.
    """
    updated, doctor = await appointments.move_to(session_id, payload.start)
    return SessionContextResponse.from_session(updated, settings, doctor)
