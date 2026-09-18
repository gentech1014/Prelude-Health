"""Integration tests for `SchedulingService` -- cancel and reschedule.

Ordering rule under test throughout: a crash (simulated here as a
concurrent write landing between two of this service's own steps) must
only ever leave lost inventory (a slot nobody holds), never double
booking (two sessions believing they hold the same slot).
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import Settings
from app.core.constants import SessionState, Sex, SlotStatus
from app.core.exceptions import InvalidSessionStateError, SlotUnavailableError
from app.integrations.google_calendar import GoogleCalendarClient
from app.models.session import PatientRef, Session
from app.models.slot import AppointmentSlot
from app.repositories.doctor_repository import DoctorRepository
from app.repositories.scheduling_event_repository import SchedulingEventRepository
from app.repositories.session_repository import SessionRepository
from app.repositories.slot_repository import SlotRepository
from app.services.scheduling_service import SchedulingService

_PHYSICIAN = "Dr. Mehta"


def _now_ms() -> datetime:
    """`datetime.now(UTC)` truncated to millisecond precision -- BSON
    stores datetimes at millisecond resolution, so a value compared
    against one that has round-tripped through Mongo must match at that
    resolution, not full microsecond precision."""
    now = datetime.now(UTC)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


@pytest.fixture
def scheduling_service(
    session_repository: SessionRepository,
    slot_repository: SlotRepository,
    scheduling_event_repository: SchedulingEventRepository,
    doctor_repository: DoctorRepository,
    calendar_client: GoogleCalendarClient,
    settings: Settings,
) -> SchedulingService:
    return SchedulingService(
        session_repository,
        slot_repository,
        scheduling_event_repository,
        doctor_repository,
        calendar_client,
        settings,
    )


async def _booked_session(
    session_repository: SessionRepository,
    slot_repository: SlotRepository,
    *,
    session_id: str,
    slot_id: str,
    starts_at: datetime,
    status: SessionState = SessionState.IN_PROGRESS,
    appointment_timezone: str = "UTC",
) -> Session:
    """Insert a session already holding a BOOKED slot -- the state both
    `cancel_appointment` and `reschedule_appointment` assume as their
    starting point."""
    now = _now_ms()
    slot = AppointmentSlot(
        slot_id=slot_id,
        physician=_PHYSICIAN,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=30),
        status=SlotStatus.BOOKED,
        session_id=session_id,
        updated_at=now,
    )
    await slot_repository.create_if_missing(slot)

    session = Session(
        session_id=session_id,
        appointment_id=f"appt_{session_id}",
        patient=PatientRef(
            name="Asha Rao", patient_id="pt_2290", date_of_birth=now, sex=Sex.FEMALE
        ),
        physician=_PHYSICIAN,
        appointment_datetime=starts_at,
        appointment_timezone=appointment_timezone,
        current_slot_id=slot_id,
        status=status,
        created_at=now,
        updated_at=now,
    )
    await session_repository.create(session)
    return session


async def test_cancel_frees_the_slot_and_marks_the_session_cancelled(
    scheduling_service: SchedulingService,
    session_repository: SessionRepository,
    slot_repository: SlotRepository,
) -> None:
    now = _now_ms()
    await _booked_session(
        session_repository,
        slot_repository,
        session_id="sess_1",
        slot_id="slot_1",
        starts_at=now + timedelta(hours=2),
    )

    updated = await scheduling_service.cancel_appointment(
        "sess_1", "changed my mind", actor="voice_agent"
    )

    assert updated.status is SessionState.CANCELLED
    assert updated.cancellation_reason == "changed my mind"
    slot = await slot_repository.get_by_id("slot_1")
    assert slot is not None
    assert slot.status is SlotStatus.FREE
    assert slot.session_id is None


async def test_cancel_is_idempotent_when_it_loses_a_race(
    scheduling_service: SchedulingService,
    session_repository: SessionRepository,
    slot_repository: SlotRepository,
) -> None:
    """The REST button and the voice tool can race on the same session --
    the loser gets the already-cancelled state back, not an error."""
    now = _now_ms()
    await _booked_session(
        session_repository,
        slot_repository,
        session_id="sess_1",
        slot_id="slot_1",
        starts_at=now + timedelta(hours=2),
    )
    first = await scheduling_service.cancel_appointment("sess_1", "reason A", actor="voice_agent")

    second = await scheduling_service.cancel_appointment("sess_1", "reason B", actor="rest_api")

    assert first.status is SessionState.CANCELLED
    assert second.status is SessionState.CANCELLED
    # The loser's reason must not overwrite the winner's.
    assert second.cancellation_reason == "reason A"


async def test_reschedule_books_new_slot_and_releases_old(
    scheduling_service: SchedulingService,
    session_repository: SessionRepository,
    slot_repository: SlotRepository,
) -> None:
    now = _now_ms()
    original_start = now + timedelta(hours=6)
    await _booked_session(
        session_repository,
        slot_repository,
        session_id="sess_1",
        slot_id="slot_old",
        starts_at=original_start,
    )
    new_start = now + timedelta(hours=2)
    await slot_repository.create_if_missing(
        AppointmentSlot(
            slot_id="slot_new",
            physician=_PHYSICIAN,
            starts_at=new_start,
            ends_at=new_start + timedelta(minutes=30),
            status=SlotStatus.FREE,
            updated_at=now,
        )
    )

    updated = await scheduling_service.reschedule_appointment(
        "sess_1", "slot_new", actor="voice_agent"
    )

    assert updated.current_slot_id == "slot_new"
    assert updated.appointment_datetime == new_start
    new_slot = await slot_repository.get_by_id("slot_new")
    old_slot = await slot_repository.get_by_id("slot_old")
    assert new_slot is not None
    assert new_slot.status is SlotStatus.BOOKED
    assert new_slot.session_id == "sess_1"
    assert old_slot is not None
    assert old_slot.status is SlotStatus.FREE
    assert old_slot.session_id is None


async def test_reschedule_to_a_slot_taken_moments_earlier_leaves_original_intact(
    scheduling_service: SchedulingService,
    session_repository: SessionRepository,
    slot_repository: SlotRepository,
) -> None:
    now = _now_ms()
    original_start = now + timedelta(hours=6)
    await _booked_session(
        session_repository,
        slot_repository,
        session_id="sess_1",
        slot_id="slot_old",
        starts_at=original_start,
    )
    new_start = now + timedelta(hours=2)
    await slot_repository.create_if_missing(
        AppointmentSlot(
            slot_id="slot_new",
            physician=_PHYSICIAN,
            starts_at=new_start,
            ends_at=new_start + timedelta(minutes=30),
            status=SlotStatus.BOOKED,
            session_id="sess_someone_else",
            updated_at=now,
        )
    )

    with pytest.raises(SlotUnavailableError):
        await scheduling_service.reschedule_appointment("sess_1", "slot_new", actor="voice_agent")

    session = await session_repository.get_by_id("sess_1")
    assert session is not None
    assert session.current_slot_id == "slot_old"
    assert session.appointment_datetime == original_start
    old_slot = await slot_repository.get_by_id("slot_old")
    assert old_slot is not None
    assert old_slot.status is SlotStatus.BOOKED  # never touched


async def test_reschedule_rejects_a_slot_not_earlier_than_current_appointment(
    scheduling_service: SchedulingService,
    session_repository: SessionRepository,
    slot_repository: SlotRepository,
) -> None:
    now = _now_ms()
    original_start = now + timedelta(hours=2)
    await _booked_session(
        session_repository,
        slot_repository,
        session_id="sess_1",
        slot_id="slot_old",
        starts_at=original_start,
    )
    later_start = now + timedelta(hours=6)
    await slot_repository.create_if_missing(
        AppointmentSlot(
            slot_id="slot_later",
            physician=_PHYSICIAN,
            starts_at=later_start,
            ends_at=later_start + timedelta(minutes=30),
            status=SlotStatus.FREE,
            updated_at=now,
        )
    )

    with pytest.raises(SlotUnavailableError):
        await scheduling_service.reschedule_appointment("sess_1", "slot_later", actor="voice_agent")


async def test_reschedule_rejects_a_slot_for_a_different_physician(
    scheduling_service: SchedulingService,
    session_repository: SessionRepository,
    slot_repository: SlotRepository,
) -> None:
    now = _now_ms()
    await _booked_session(
        session_repository,
        slot_repository,
        session_id="sess_1",
        slot_id="slot_old",
        starts_at=now + timedelta(hours=6),
    )
    new_start = now + timedelta(hours=2)
    await slot_repository.create_if_missing(
        AppointmentSlot(
            slot_id="slot_other_doctor",
            physician="Dr. Someone Else",
            starts_at=new_start,
            ends_at=new_start + timedelta(minutes=30),
            status=SlotStatus.FREE,
            updated_at=now,
        )
    )

    with pytest.raises(SlotUnavailableError):
        await scheduling_service.reschedule_appointment(
            "sess_1", "slot_other_doctor", actor="voice_agent"
        )


async def test_reschedule_compensates_when_session_is_cancelled_mid_flight(
    scheduling_service: SchedulingService,
    session_repository: SessionRepository,
    slot_repository: SlotRepository,
) -> None:
    """Simulates a cancel landing between the new slot's claim and the
    session update: the new slot must be released back to FREE rather
    than left HELD forever, and the original (now-cancelled) session must
    not be silently resurrected."""
    now = _now_ms()
    await _booked_session(
        session_repository,
        slot_repository,
        session_id="sess_1",
        slot_id="slot_old",
        starts_at=now + timedelta(hours=6),
    )
    new_start = now + timedelta(hours=2)
    await slot_repository.create_if_missing(
        AppointmentSlot(
            slot_id="slot_new",
            physician=_PHYSICIAN,
            starts_at=new_start,
            ends_at=new_start + timedelta(minutes=30),
            status=SlotStatus.FREE,
            updated_at=now,
        )
    )
    # Simulate the race: cancel the session directly, out from under the
    # reschedule call that is about to run.
    await session_repository.set_cancelled(
        "sess_1", now, "cancelled mid-flight", expected_status=frozenset({SessionState.IN_PROGRESS})
    )

    with pytest.raises(InvalidSessionStateError):
        await scheduling_service.reschedule_appointment("sess_1", "slot_new", actor="voice_agent")

    new_slot = await slot_repository.get_by_id("slot_new")
    assert new_slot is not None
    assert new_slot.status is SlotStatus.FREE  # released, not left HELD forever
    session = await session_repository.get_by_id("sess_1")
    assert session is not None
    assert session.status is SessionState.CANCELLED  # not resurrected


async def test_find_earlier_slots_returns_prescreened_local_times(
    scheduling_service: SchedulingService,
    session_repository: SessionRepository,
    slot_repository: SlotRepository,
) -> None:
    now = _now_ms()
    await _booked_session(
        session_repository,
        slot_repository,
        session_id="sess_1",
        slot_id="slot_old",
        starts_at=now + timedelta(hours=6),
        appointment_timezone="Asia/Kolkata",
    )
    earlier_start = now + timedelta(hours=2)
    await slot_repository.create_if_missing(
        AppointmentSlot(
            slot_id="slot_earlier",
            physician=_PHYSICIAN,
            starts_at=earlier_start,
            ends_at=earlier_start + timedelta(minutes=30),
            status=SlotStatus.FREE,
            updated_at=now,
        )
    )

    offers = await scheduling_service.find_earlier_slots("sess_1")

    assert len(offers) == 1
    assert offers[0].slot_id == "slot_earlier"
    assert "at" in offers[0].spoken_time  # a formatted phrase, never a raw timestamp
    assert "+00:00" not in offers[0].spoken_time  # i.e. definitely not an ISO string
    assert str(earlier_start.year) not in offers[0].spoken_time


async def test_find_earlier_slots_excludes_slots_within_the_minimum_lead_time(
    scheduling_service: SchedulingService,
    session_repository: SessionRepository,
    slot_repository: SlotRepository,
    settings: Settings,
) -> None:
    now = _now_ms()
    await _booked_session(
        session_repository,
        slot_repository,
        session_id="sess_1",
        slot_id="slot_old",
        starts_at=now + timedelta(hours=6),
    )
    too_soon = now + timedelta(minutes=settings.reschedule_min_lead_minutes - 1)
    await slot_repository.create_if_missing(
        AppointmentSlot(
            slot_id="slot_too_soon",
            physician=_PHYSICIAN,
            starts_at=too_soon,
            ends_at=too_soon + timedelta(minutes=30),
            status=SlotStatus.FREE,
            updated_at=now,
        )
    )

    offers = await scheduling_service.find_earlier_slots("sess_1")

    assert offers == []
