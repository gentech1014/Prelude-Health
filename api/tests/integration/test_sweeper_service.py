"""Integration tests for `SweeperService` and the multi-replica lease.

Every rule under test is a pure function of one slot and, at most, the
one session it references -- no transaction, safe to run concurrently
with live calls and repeatedly after a partial failure.
"""

from datetime import UTC, datetime, timedelta

from app.core.config import Settings
from app.core.constants import SessionState, Sex, SlotStatus
from app.models.session import PatientRef, Session
from app.models.slot import AppointmentSlot
from app.repositories.scheduling_event_repository import SchedulingEventRepository
from app.repositories.session_repository import SessionRepository
from app.repositories.slot_repository import SlotRepository
from app.repositories.sweeper_lock_repository import SweeperLockRepository
from app.services.sweeper_service import SweeperService

_PHYSICIAN = "Dr. Mehta"


def _now_ms() -> datetime:
    """See the identical helper in test_scheduling_service.py -- BSON's
    millisecond resolution means a value compared after a Mongo
    round-trip must match at that resolution."""
    now = datetime.now(UTC)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


async def _insert_session(
    session_repository: SessionRepository,
    *,
    session_id: str,
    status: SessionState,
    appointment_datetime: datetime,
    updated_at: datetime,
    created_at: datetime | None = None,
) -> None:
    session = Session(
        session_id=session_id,
        appointment_id=f"appt_{session_id}",
        patient=PatientRef(
            name="Asha Rao", patient_id="pt_2290", date_of_birth=_now_ms(), sex=Sex.FEMALE
        ),
        physician=_PHYSICIAN,
        appointment_datetime=appointment_datetime,
        status=status,
        created_at=created_at or updated_at,
        updated_at=updated_at,
    )
    await session_repository.create(session)


async def _insert_slot(
    slot_repository: SlotRepository,
    *,
    slot_id: str,
    status: SlotStatus,
    starts_at: datetime,
    session_id: str | None = None,
    held_until: datetime | None = None,
) -> None:
    slot = AppointmentSlot(
        slot_id=slot_id,
        physician=_PHYSICIAN,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=30),
        status=status,
        session_id=session_id,
        held_until=held_until,
        updated_at=_now_ms(),
    )
    await slot_repository.create_if_missing(slot)


async def test_reclaims_a_slot_left_booked_by_an_abandoned_call(
    session_repository: SessionRepository,
    slot_repository: SlotRepository,
    scheduling_event_repository: SchedulingEventRepository,
    settings: Settings,
) -> None:
    stale = _now_ms() - timedelta(minutes=settings.abandoned_slot_reclaim_minutes + 5)
    await _insert_session(
        session_repository,
        session_id="sess_abandoned",
        status=SessionState.IN_PROGRESS,
        appointment_datetime=_now_ms() + timedelta(hours=2),
        updated_at=stale,
    )
    await _insert_slot(
        slot_repository,
        slot_id="slot_1",
        status=SlotStatus.BOOKED,
        starts_at=_now_ms() + timedelta(hours=2),
        session_id="sess_abandoned",
    )
    sweeper = SweeperService(
        session_repository, slot_repository, scheduling_event_repository, settings
    )

    result = await sweeper.run_once()

    assert result.reclaimed_abandoned_slots == 1
    slot = await slot_repository.get_by_id("slot_1")
    assert slot is not None
    assert slot.status is SlotStatus.FREE
    session = await session_repository.get_by_id("sess_abandoned")
    assert session is not None
    assert session.status is SessionState.EXPIRED


async def test_does_not_reclaim_a_slot_whose_call_is_still_active(
    session_repository: SessionRepository,
    slot_repository: SlotRepository,
    scheduling_event_repository: SchedulingEventRepository,
    settings: Settings,
) -> None:
    await _insert_session(
        session_repository,
        session_id="sess_live",
        status=SessionState.IN_PROGRESS,
        appointment_datetime=_now_ms() + timedelta(hours=2),
        updated_at=_now_ms(),  # just wrote a transcript turn -- not stale
    )
    await _insert_slot(
        slot_repository,
        slot_id="slot_1",
        status=SlotStatus.BOOKED,
        starts_at=_now_ms() + timedelta(hours=2),
        session_id="sess_live",
    )
    sweeper = SweeperService(
        session_repository, slot_repository, scheduling_event_repository, settings
    )

    result = await sweeper.run_once()

    assert result.reclaimed_abandoned_slots == 0
    slot = await slot_repository.get_by_id("slot_1")
    assert slot is not None
    assert slot.status is SlotStatus.BOOKED


async def test_resets_an_expired_transient_hold_back_to_free(
    session_repository: SessionRepository,
    slot_repository: SlotRepository,
    scheduling_event_repository: SchedulingEventRepository,
    settings: Settings,
) -> None:
    """Correctness never depended on this -- `list_bookable` already
    treats an expired hold as takeable -- this rule only tidies storage."""
    await _insert_slot(
        slot_repository,
        slot_id="slot_1",
        status=SlotStatus.HELD,
        starts_at=_now_ms() + timedelta(hours=2),
        session_id="sess_abandoned_reschedule",
        held_until=_now_ms() - timedelta(seconds=1),
    )
    sweeper = SweeperService(
        session_repository, slot_repository, scheduling_event_repository, settings
    )

    result = await sweeper.run_once()

    assert result.reset_expired_holds == 1
    slot = await slot_repository.get_by_id("slot_1")
    assert slot is not None
    assert slot.status is SlotStatus.FREE


async def test_does_not_reset_a_hold_that_has_not_expired_yet(
    session_repository: SessionRepository,
    slot_repository: SlotRepository,
    scheduling_event_repository: SchedulingEventRepository,
    settings: Settings,
) -> None:
    await _insert_slot(
        slot_repository,
        slot_id="slot_1",
        status=SlotStatus.HELD,
        starts_at=_now_ms() + timedelta(hours=2),
        session_id="sess_mid_reschedule",
        held_until=_now_ms() + timedelta(seconds=60),
    )
    sweeper = SweeperService(
        session_repository, slot_repository, scheduling_event_repository, settings
    )

    result = await sweeper.run_once()

    assert result.reset_expired_holds == 0
    slot = await slot_repository.get_by_id("slot_1")
    assert slot is not None
    assert slot.status is SlotStatus.HELD


async def test_expires_a_session_whose_appointment_time_has_passed(
    session_repository: SessionRepository,
    slot_repository: SlotRepository,
    scheduling_event_repository: SchedulingEventRepository,
    settings: Settings,
) -> None:
    await _insert_session(
        session_repository,
        session_id="sess_never_opened",
        status=SessionState.NOTIFICATION_SENT,
        appointment_datetime=_now_ms() - timedelta(hours=1),  # already passed
        updated_at=_now_ms() - timedelta(days=1),
    )
    sweeper = SweeperService(
        session_repository, slot_repository, scheduling_event_repository, settings
    )

    result = await sweeper.run_once()

    assert result.expired_sessions == 1
    session = await session_repository.get_by_id("sess_never_opened")
    assert session is not None
    assert session.status is SessionState.EXPIRED


async def test_expires_a_session_past_the_intake_link_ttl_even_before_the_appointment(
    session_repository: SessionRepository,
    slot_repository: SlotRepository,
    scheduling_event_repository: SchedulingEventRepository,
    settings: Settings,
) -> None:
    stale_created = _now_ms() - timedelta(seconds=settings.intake_link_ttl_seconds + 60)
    await _insert_session(
        session_repository,
        session_id="sess_link_expired",
        status=SessionState.STARTED,
        appointment_datetime=_now_ms() + timedelta(days=3),  # appointment is still in the future
        updated_at=stale_created,
        created_at=stale_created,
    )
    sweeper = SweeperService(
        session_repository, slot_repository, scheduling_event_repository, settings
    )

    result = await sweeper.run_once()

    assert result.expired_sessions == 1


async def test_does_not_expire_a_session_still_within_its_window(
    session_repository: SessionRepository,
    slot_repository: SlotRepository,
    scheduling_event_repository: SchedulingEventRepository,
    settings: Settings,
) -> None:
    await _insert_session(
        session_repository,
        session_id="sess_fresh",
        status=SessionState.NOTIFICATION_SENT,
        appointment_datetime=_now_ms() + timedelta(days=1),
        updated_at=_now_ms(),
    )
    sweeper = SweeperService(
        session_repository, slot_repository, scheduling_event_repository, settings
    )

    result = await sweeper.run_once()

    assert result.expired_sessions == 0
    session = await session_repository.get_by_id("sess_fresh")
    assert session is not None
    assert session.status is SessionState.NOTIFICATION_SENT


async def test_lease_blocks_a_second_concurrent_acquire(
    sweeper_lock_repository: SweeperLockRepository,
) -> None:
    """Multi-replica safety: exactly one caller wins per tick."""
    first = await sweeper_lock_repository.try_acquire("replica_a", lease_seconds=120)

    second = await sweeper_lock_repository.try_acquire("replica_b", lease_seconds=120)

    assert first is True
    assert second is False


async def test_lease_can_be_reacquired_once_it_expires(
    sweeper_lock_repository: SweeperLockRepository,
) -> None:
    await sweeper_lock_repository.try_acquire("replica_a", lease_seconds=-1)  # already expired

    reacquired = await sweeper_lock_repository.try_acquire("replica_b", lease_seconds=120)

    assert reacquired is True
