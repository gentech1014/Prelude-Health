"""Cancel / reschedule orchestration -- the single implementation the
voice tools (`app.agents.tools.scheduling`) and the patient-facing REST
routes both call into.

Ordering rule used throughout, per the design note in the plan this
implements: order every write so a crash can only leave LOST INVENTORY (a
slot stuck held by nobody), never DOUBLE BOOKING (two sessions believing
they hold the same slot). Lost inventory is recoverable by
`SweeperService`; double booking is not.
"""

from datetime import UTC, datetime, timedelta

import structlog

from app.core.config import Settings
from app.core.constants import SessionState, SlotStatus
from app.core.exceptions import (
    InvalidSessionStateError,
    SessionNotFoundError,
    SlotUnavailableError,
)
from app.core.speech_time import format_spoken_time
from app.integrations.google_calendar import GoogleCalendarClient
from app.models.session import Session
from app.models.slot import AppointmentSlot, SlotOffer
from app.repositories.doctor_repository import DoctorRepository
from app.repositories.scheduling_event_repository import (
    SchedulingAction,
    SchedulingActor,
    SchedulingEvent,
    SchedulingEventRepository,
)
from app.repositories.session_repository import SessionRepository
from app.repositories.slot_repository import SlotRepository

log = structlog.get_logger(__name__)

# A session holding a slot can only be cancelled or rescheduled while it
# is still an active, unfinished visit -- never after it has already
# completed, failed, or been escalated. STARTED is included: a patient
# who opened the link but has not yet given consent can still cancel via
# the REST button before ever reaching the live call.
_ACTIVE_STATES = frozenset(
    {
        SessionState.NOTIFICATION_SENT,
        SessionState.STARTED,
        SessionState.IN_PROGRESS,
    }
)


class SchedulingService:
    """Coordinates cancel/reschedule across `Session`, `AppointmentSlot`,
    the audit trail, and best-effort Calendar delivery."""

    def __init__(
        self,
        session_repository: SessionRepository,
        slot_repository: SlotRepository,
        scheduling_events: SchedulingEventRepository,
        doctor_repository: DoctorRepository,
        calendar_client: GoogleCalendarClient,
        settings: Settings,
    ) -> None:
        self._sessions = session_repository
        self._slots = slot_repository
        self._events = scheduling_events
        self._doctors = doctor_repository
        self._calendar = calendar_client
        self._settings = settings

    async def cancel_appointment(
        self, session_id: str, reason: str | None, *, actor: SchedulingActor
    ) -> Session:
        """Cancel a session's appointment and free its slot.

        Idempotent: if the REST button and the voice tool race, the loser
        simply gets back the (already-cancelled) current state rather
        than an error -- there is nothing wrong with a request that
        arrives to find its goal already achieved.
        """
        session = await self._sessions.get_by_id(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)

        updated = await self._sessions.set_cancelled(
            session_id, datetime.now(UTC), reason, expected_status=_ACTIVE_STATES
        )
        if updated is None:
            # Someone already won this race (or the session was never
            # cancellable) -- return current state rather than erroring.
            current = await self._sessions.get_by_id(session_id)
            if current is None:
                raise SessionNotFoundError(session_id)
            log.info("cancel_appointment_already_done", session_id=session_id, actor=actor)
            return current

        if updated.current_slot_id is not None:
            released = await self._slots.release(
                updated.current_slot_id, session_id, expected_status=SlotStatus.BOOKED
            )
            if released:
                await self._record_event(
                    action="released",
                    slot_id=updated.current_slot_id,
                    session_id=session_id,
                    physician=updated.physician,
                    starts_at=updated.appointment_datetime,
                    from_status=SlotStatus.BOOKED,
                    to_status=SlotStatus.FREE,
                    actor=actor,
                    reason=reason,
                )

        await self._deliver_cancellation_to_calendar(updated)
        log.warning("appointment_cancelled", session_id=session_id, actor=actor, reason=reason)
        return updated

    async def find_earlier_slots(self, session_id: str) -> list[SlotOffer]:
        """Bookable slots for this doctor, strictly earlier than the
        patient's current appointment. Never raises -- an empty list
        means "say nothing," not "something went wrong."""
        session = await self._sessions.get_by_id(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)

        now = datetime.now(UTC)
        after = now + timedelta(minutes=self._settings.reschedule_min_lead_minutes)
        slots = await self._slots.list_bookable(
            session.physician,
            after=after,
            before=session.appointment_datetime,
            limit=self._settings.reschedule_offer_limit,
        )
        return [
            SlotOffer(
                slot_id=s.slot_id,
                spoken_time=format_spoken_time(s.starts_at, session.appointment_timezone),
            )
            for s in slots
        ]

    async def reschedule_appointment(
        self, session_id: str, slot_id: str, *, actor: SchedulingActor
    ) -> Session:
        """Move a session onto an earlier slot, releasing its old one.

        Three single-document writes (claim new -> update session ->
        promote + release old), ordered so every crash window leaves
        either the patient's original appointment fully intact, or lost
        inventory `SweeperService` can reclaim -- never two sessions
        holding the same slot. Raises `SlotUnavailableError` if the slot
        is invalid, already taken, not earlier than the current
        appointment, or belongs to a different physician; the caller
        keeps their original appointment in every one of those cases.
        """
        session = await self._sessions.get_by_id(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        if session.current_slot_id is None:
            raise SlotUnavailableError(slot_id, "this session holds no appointment slot to move")

        new_slot = await self._slots.get_by_id(slot_id)
        if new_slot is None:
            raise SlotUnavailableError(slot_id, "no such slot")
        if new_slot.physician != session.physician:
            raise SlotUnavailableError(slot_id, "belongs to a different physician")

        now = datetime.now(UTC)
        after = now + timedelta(minutes=self._settings.reschedule_min_lead_minutes)
        held_until = now + timedelta(seconds=self._settings.slot_hold_ttl_seconds)

        claimed = await self._slots.claim(
            slot_id,
            session.physician,
            session_id,
            after=after,
            before=session.appointment_datetime,
            held_until=held_until,
        )
        if not claimed:
            raise SlotUnavailableError(
                slot_id, "just taken, or no longer earlier than the current appointment"
            )
        await self._record_event(
            action="rescheduled_in",
            slot_id=slot_id,
            session_id=session_id,
            physician=new_slot.physician,
            starts_at=new_slot.starts_at,
            from_status=SlotStatus.FREE,
            to_status=SlotStatus.HELD,
            actor=actor,
            reason=None,
        )

        old_slot_id = session.current_slot_id
        updated = await self._sessions.set_rescheduled(
            session_id,
            new_slot.starts_at,
            session.appointment_timezone,
            slot_id,
            expected_status=_ACTIVE_STATES,
            expected_slot_id=old_slot_id,
        )
        if updated is None:
            # The session moved on concurrently (e.g. cancelled while this
            # claim was in flight). Compensate: release the just-claimed
            # HELD slot back to FREE -- safe, because the release filter
            # proves we still own it -- and report failure rather than
            # silently leaving a HELD slot nobody will ever promote.
            await self._slots.release(slot_id, session_id, expected_status=SlotStatus.HELD)
            current = await self._sessions.get_by_id(session_id)
            current_status = current.status if current is not None else "unknown"
            raise InvalidSessionStateError(
                session_id, str(current_status), " or ".join(sorted(_ACTIVE_STATES))
            )

        await self._slots.promote(slot_id, session_id)
        await self._record_event(
            action="booked",
            slot_id=slot_id,
            session_id=session_id,
            physician=new_slot.physician,
            starts_at=new_slot.starts_at,
            from_status=SlotStatus.HELD,
            to_status=SlotStatus.BOOKED,
            actor=actor,
            reason=None,
        )

        released = await self._slots.release(
            old_slot_id, session_id, expected_status=SlotStatus.BOOKED
        )
        if released:
            await self._record_event(
                action="rescheduled_out",
                slot_id=old_slot_id,
                session_id=session_id,
                physician=session.physician,
                starts_at=session.appointment_datetime,
                from_status=SlotStatus.BOOKED,
                to_status=SlotStatus.FREE,
                actor=actor,
                reason=None,
            )

        await self._deliver_reschedule_to_calendar(updated, new_slot)
        log.info(
            "appointment_rescheduled",
            session_id=session_id,
            actor=actor,
            old_slot_id=old_slot_id,
            new_slot_id=slot_id,
        )
        return updated

    async def _deliver_cancellation_to_calendar(self, session: Session) -> None:
        """Best-effort: delete the doctor's calendar event for a cancelled
        appointment. Never blocks or raises -- matching every other
        Calendar delivery method in this codebase."""
        if not session.calendar_event_id:
            return
        doctor = await self._doctors.get_by_name(session.physician)
        if doctor is None:
            log.warning("cancel_calendar_skipped_unknown_doctor", session_id=session.session_id)
            return
        await self._calendar.delete_event(doctor.google_calendar_id, session.calendar_event_id)

    async def _deliver_reschedule_to_calendar(
        self, session: Session, new_slot: AppointmentSlot
    ) -> None:
        """Best-effort: move the doctor's calendar event to the new time."""
        if not session.calendar_event_id:
            return
        doctor = await self._doctors.get_by_name(session.physician)
        if doctor is None:
            log.warning("reschedule_calendar_skipped_unknown_doctor", session_id=session.session_id)
            return
        await self._calendar.update_event_time(
            doctor.google_calendar_id,
            session.calendar_event_id,
            new_slot.starts_at,
            session.appointment_timezone,
            timedelta(minutes=self._settings.slot_duration_minutes),
        )

    async def _record_event(
        self,
        *,
        action: SchedulingAction,
        slot_id: str | None,
        session_id: str | None,
        physician: str,
        starts_at: datetime,
        from_status: SlotStatus | None,
        to_status: SlotStatus,
        actor: SchedulingActor,
        reason: str | None,
    ) -> None:
        """Write one audit record, after the guarded write it documents,
        never before. Swallow-and-log, matching this codebase's existing
        contract for `MongoTranscriptWriter` and Calendar delivery: an
        audit record for a write that already succeeded must never be
        allowed to undo it."""
        version_after = 0
        if slot_id is not None:
            slot = await self._slots.get_by_id(slot_id)
            version_after = slot.version if slot is not None else 0
        try:
            await self._events.record(
                SchedulingEvent(
                    action=action,
                    slot_id=slot_id,
                    session_id=session_id,
                    physician=physician,
                    starts_at=starts_at,
                    from_status=from_status.value if from_status is not None else None,
                    to_status=to_status.value,
                    version_after=version_after,
                    actor=actor,
                    reason=reason,
                )
            )
        except Exception:
            log.exception("scheduling_event_write_failed", action=action, slot_id=slot_id)
