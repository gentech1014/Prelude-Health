"""Periodic reconciliation: reclaims slots an abandoned call left BOOKED
forever, tidies transient reschedule holds that already self-expired, and
finally gives the long-dead `SessionState.EXPIRED` a writer.

No scheduler dependency exists in this project (no APScheduler/celery, no
k8s CronJob, a single unreplicated uvicorn process) and none is added for
this: `run_once()` is pure logic with no loop, no sleep, no lock, driven
either by a lease-guarded lifespan task (`app.main`) or manually via
`scripts/sweep_slots.py`. Every rule here is a pure function of one slot
and, at most, the one session it references -- which is exactly what
makes it safe to run on any schedule, concurrently with live calls, and
repeatedly after a partial failure with no transaction required.
"""

from datetime import UTC, datetime, timedelta

import structlog
from pydantic import BaseModel

from app.core.config import Settings
from app.core.constants import SessionState, SlotStatus
from app.repositories.scheduling_event_repository import SchedulingEvent, SchedulingEventRepository
from app.repositories.session_repository import SessionRepository
from app.repositories.slot_repository import SlotRepository

log = structlog.get_logger(__name__)

_SWEEP_BATCH_LIMIT = 200
"""Caps every query this service runs so one bad tick cannot stall the
event loop that also carries live patient audio."""


class SweepResult(BaseModel):
    """Counts from one `run_once()` pass, for logging and tests."""

    reclaimed_abandoned_slots: int = 0
    reset_expired_holds: int = 0
    expired_sessions: int = 0


class SweeperService:
    """Stateless reconciliation logic. No loop, no sleep, no lock --
    those live in the lifespan task / CLI that calls `run_once()`."""

    def __init__(
        self,
        session_repository: SessionRepository,
        slot_repository: SlotRepository,
        scheduling_events: SchedulingEventRepository,
        settings: Settings,
    ) -> None:
        self._sessions = session_repository
        self._slots = slot_repository
        self._events = scheduling_events
        self._settings = settings

    async def run_once(self) -> SweepResult:
        """Run every reconciliation rule once and return what it did."""
        reclaimed = await self._reclaim_abandoned_holds()
        reset = await self._reset_expired_holds()
        expired = await self._expire_stale_sessions()
        result = SweepResult(
            reclaimed_abandoned_slots=reclaimed, reset_expired_holds=reset, expired_sessions=expired
        )
        log.info("sweep_tick_completed", **result.model_dump())
        return result

    async def _reclaim_abandoned_holds(self) -> int:
        """A BOOKED slot whose session is still IN_PROGRESS but has not
        written a transcript turn in `abandoned_slot_reclaim_minutes` is
        the abandoned-WebSocket case: `intake.py` only ever writes to
        Mongo on a raised `WebSocketDisconnect`, which SIGKILL, OOM, an
        ECS task replacement, or a half-open socket all bypass entirely.
        This is the one place that reclaims the slot AND finally writes
        the session to EXPIRED -- one root cause, one fix, not two.

        Stated honestly: `updated_at` is not a true liveness signal. It
        only advances on final transcript turns, and the live prompt
        actively sends patients away from the mic (e.g. to fetch a
        medication bottle) -- so a silent-but-connected patient looks
        identical to a dead process. Accepted tradeoff for this project.
        """
        threshold = datetime.now(UTC) - timedelta(
            minutes=self._settings.abandoned_slot_reclaim_minutes
        )
        reclaimed = 0
        for slot in await self._slots.list_by_status(SlotStatus.BOOKED, limit=_SWEEP_BATCH_LIMIT):
            if slot.session_id is None:
                continue
            session = await self._sessions.get_by_id(slot.session_id)
            if session is None or session.status != SessionState.IN_PROGRESS:
                continue
            if session.updated_at > threshold:
                continue

            released = await self._slots.release(
                slot.slot_id, slot.session_id, expected_status=SlotStatus.BOOKED
            )
            if not released:
                continue

            await self._sessions.set_status_guarded(
                slot.session_id,
                SessionState.EXPIRED,
                expected_status=frozenset({SessionState.IN_PROGRESS}),
            )
            await self._record_swept(slot.slot_id, slot.session_id, slot.physician, slot.starts_at)
            reclaimed += 1
        return reclaimed

    async def _reset_expired_holds(self) -> int:
        """A HELD slot whose `held_until` has already passed is already
        treated as bookable by every availability query (see
        `SlotRepository.list_bookable`/`claim`) -- this rule does not fix
        correctness, it tidies storage so a direct status query (and the
        audit trail) reflect reality without needing the `$or` predicate
        to explain it."""
        now = datetime.now(UTC)
        reset = 0
        for slot in await self._slots.list_by_status(SlotStatus.HELD, limit=_SWEEP_BATCH_LIMIT):
            if slot.held_until is None or slot.held_until > now:
                continue
            if slot.session_id is None:
                continue
            released = await self._slots.release(
                slot.slot_id, slot.session_id, expected_status=SlotStatus.HELD
            )
            if released:
                reset += 1
        return reset

    async def _expire_stale_sessions(self) -> int:
        """A session that never made it into a live call, past either its
        own appointment time or the intake link's TTL window, is
        `EXPIRED` -- the definition README already documents for this
        state; it simply had no writer until now."""
        now = datetime.now(UTC)
        created_before = now - timedelta(seconds=self._settings.intake_link_ttl_seconds)
        expired = 0
        for session in await self._sessions.list_expirable(
            appointment_before=now, created_before=created_before, limit=_SWEEP_BATCH_LIMIT
        ):
            updated = await self._sessions.set_status_guarded(
                session.session_id,
                SessionState.EXPIRED,
                expected_status=frozenset({session.status}),
            )
            if updated is not None:
                expired += 1
        return expired

    async def _record_swept(
        self, slot_id: str, session_id: str, physician: str, starts_at: datetime
    ) -> None:
        """Swallow-and-log, matching every other audit write in this
        feature: an audit record for a reclaim that already succeeded
        must never undo it."""
        slot = await self._slots.get_by_id(slot_id)
        try:
            await self._events.record(
                SchedulingEvent(
                    action="swept_abandoned",
                    slot_id=slot_id,
                    session_id=session_id,
                    physician=physician,
                    starts_at=starts_at,
                    from_status=SlotStatus.BOOKED.value,
                    to_status=SlotStatus.FREE.value,
                    version_after=slot.version if slot is not None else 0,
                    actor="sweeper",
                    reason=None,
                )
            )
        except Exception:
            log.exception(
                "scheduling_event_write_failed", action="swept_abandoned", slot_id=slot_id
            )
