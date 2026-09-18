"""One patient's own appointment: the times it can move to, and moving it.

Extracted from `app.api.v1.appointments` when the live agent gained the
same two abilities. The REST routes are what the patient's screen calls
when they tap a time; the tools in `app.agents.tools.appointment` are what
the agent calls when they say one out loud. Both are the same conversation
and must never disagree about which times are open or what happens when
one is taken, so both go through here.

Deliberately *not* `SchedulingService`, which moves a session between rows
in the `appointment_slots` collection and only ever onto an earlier one.
This is the patient-facing path the reschedule screen has always used:
availability is derived from clinic hours filtered by the doctor's real
commitments (`AvailabilityService`), and any open time in the horizon is
offerable -- a patient asking to move their appointment usually wants it
later, not sooner.
"""

from datetime import UTC, date, datetime, tzinfo

import structlog

from app.core.constants import SessionState
from app.core.exceptions import (
    AppointmentTimeUnavailableError,
    DoctorNotFoundError,
    InvalidSessionStateError,
    SessionNotFoundError,
)
from app.core.speech_time import format_spoken_datetime
from app.integrations.calendar_delivery import CalendarDelivery
from app.models.doctor import Doctor
from app.models.session import Session
from app.repositories.doctor_repository import DoctorRepository
from app.repositories.session_repository import SessionRepository
from app.services.availability_service import AvailabilityService, AvailableDay

log = structlog.get_logger(__name__)

RESCHEDULABLE_STATES = frozenset(
    {
        SessionState.NOTIFICATION_SENT,
        SessionState.STARTED,
        SessionState.IN_PROGRESS,
        SessionState.INTERRUPTED,
        SessionState.COMPLETED,
        SessionState.SUMMARIZING,
        SessionState.SUMMARY_READY,
        SessionState.SUMMARY_FAILED,
        SessionState.VIDEO_READY,
    }
)
"""States a patient may still move their appointment from.

A completed call is included on purpose: the prescreening being finished
does not stop the appointment itself needing to move, and that is exactly
where the patient app offers it. DECLINED and EXPIRED are excluded --
there is no live appointment left to move."""


class SpokenSlot:
    """One open time, as the agent has to say it and pass it back.

    `slot_id` is the ISO start the REST route already treats as a slot's
    identity, so the time the agent offers and the time the screen offers
    are the same string. It is never spoken: `when` is, and it is rendered
    server-side in clinic-local time so the model never converts a
    timestamp itself.
    """

    def __init__(self, start: datetime, spoken_time: str) -> None:
        self.start = start
        self.slot_id = start.isoformat()
        self.spoken_time = spoken_time


class AppointmentService:
    """Reads and moves the appointment behind one prescreening session."""

    def __init__(
        self,
        session_repository: SessionRepository,
        doctor_repository: DoctorRepository,
        availability_service: AvailabilityService,
        calendar: CalendarDelivery,
    ) -> None:
        self._sessions = session_repository
        self._doctors = doctor_repository
        self._availability = availability_service
        self._calendar = calendar

    @property
    def clinic_timezone(self) -> tzinfo:
        """The clinic's own timezone, which every offered time is rendered in."""
        return self._availability.clinic_zone

    async def open_days(
        self, session_id: str, from_day: date | None = None, days: int | None = None
    ) -> tuple[Doctor, list[AvailableDay]]:
        """Open times with this session's own doctor, day by day.

        Raises `SessionNotFoundError` for an unknown session and
        `DoctorNotFoundError` when the session's `physician` string matches
        no doctor on file -- sessions name their physician as free text, so
        a typo at booking genuinely leaves nothing to look up.
        """
        session = await self._require_session(session_id)
        doctor = await self._require_doctor(session)
        return doctor, await self._availability.days_for_doctor(
            doctor, from_day=from_day, days=days
        )

    async def spoken_slots(self, session_id: str, limit: int) -> list[SpokenSlot]:
        """The soonest open times, flattened and rendered for speech.

        Flattened across days and capped, because this is read aloud: the
        screen can show a fortnight of times and let the patient scroll,
        but a voice agent listing more than a handful has stopped being
        usable. The cap is the caller's, so the tool decides how much of
        the list the model is allowed to hear.

        The session's own current time is excluded -- offering a patient
        the slot they already hold as a change reads as the agent not
        knowing when their appointment is.
        """
        session = await self._require_session(session_id)
        doctor = await self._require_doctor(session)
        days = await self._availability.days_for_doctor(doctor)

        current = _as_utc(session.appointment_datetime)
        offered: list[SpokenSlot] = []
        for day in days:
            for slot in day.slots:
                if _as_utc(slot.start) == current:
                    continue
                offered.append(
                    SpokenSlot(
                        start=slot.start,
                        spoken_time=format_spoken_datetime(
                            slot.start, session.appointment_timezone
                        ),
                    )
                )
                if len(offered) == limit:
                    return offered
        return offered

    async def move_to(self, session_id: str, start: datetime) -> tuple[Session, Doctor]:
        """Move this session's appointment to `start`.

        The time is re-checked against live availability rather than
        trusted from the caller: both callers work from a snapshot the
        patient may have been reading for a while, and someone else may
        have taken it since. That raises
        `AppointmentTimeUnavailableError`, which the REST route surfaces
        as a 409 and the tool turns into a sentence the agent can say.

        The calendar update is best-effort and happens after the session
        is written. The stored appointment is what the intake link, the
        report and the agent's own greeting all read from, so it is the
        value that must land; a Calendar hiccup costs the doctor a stale
        entry, not the patient's booking.
        """
        session = await self._require_session(session_id)
        if session.status not in RESCHEDULABLE_STATES:
            raise InvalidSessionStateError(
                session_id, session.status, "an active appointment state"
            )

        doctor = await self._require_doctor(session)
        if not await self._availability.is_slot_bookable(doctor, start):
            raise AppointmentTimeUnavailableError(doctor.name)

        updated = await self._sessions.set_appointment_datetime(session_id, start)
        if updated is None:
            raise SessionNotFoundError(session_id)

        if updated.calendar_event_id:
            await self._calendar.move_event(doctor, updated.calendar_event_id, start)

        log.info(
            "appointment_rescheduled",
            session_id=session_id,
            # The new time is not PHI on its own, and an operator investigating a
            # double-booking needs it. The patient's identity is not logged with it.
            scheduled_at=start.isoformat(),
        )
        return updated, doctor

    async def _require_session(self, session_id: str) -> Session:
        session = await self._sessions.get_by_id(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        return session

    async def _require_doctor(self, session: Session) -> Doctor:
        doctor = await self._doctors.get_by_name(session.physician)
        if doctor is None:
            raise DoctorNotFoundError(session.physician)
        return doctor


def _as_utc(value: datetime) -> datetime:
    """Normalize to aware UTC, since Mongo hands back naive datetimes."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
