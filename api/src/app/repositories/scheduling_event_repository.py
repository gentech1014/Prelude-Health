"""Data access for the `scheduling_events` collection -- append-only audit trail.

A separate collection, never an array embedded on the slot: slot
retention (short, storage hygiene) and audit retention (long, a clinical
record of who changed what) are different lifetimes, and an embedded
array would grow on the single most-contended document in the whole
design. `physician`/`starts_at` are denormalized on purpose, so an event
stays readable after its slot has been purged by the slot TTL index.
"""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.repositories.base import MongoRepository

_NO_MONGO_ID = {"_id": 0}

SchedulingAction = Literal[
    "opened", "booked", "released", "rescheduled_out", "rescheduled_in", "swept_abandoned", "purged"
]
SchedulingActor = Literal["booking_webhook", "voice_agent", "rest_api", "sweeper", "seed"]


class SchedulingEvent(BaseModel):
    """One append-only audit record of a slot/session state change.

    `version_after` is load-bearing, not decorative: this insert is a
    second, non-atomic round trip after the guarded write it records, so
    it can be lost on a crash between the two. Storing the slot's version
    makes that loss *detectable* -- an audit stream reading 1, 2, 4 says
    event 3 never landed. Without it, a lost event is indistinguishable
    from no event ever having happened.
    """

    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    action: SchedulingAction
    slot_id: str | None
    session_id: str | None
    physician: str
    starts_at: datetime
    from_status: str | None
    to_status: str
    version_after: int
    actor: SchedulingActor
    reason: str | None = None


class SchedulingEventRepository(MongoRepository):
    """Append-only writes and per-entity reads for the scheduling audit trail."""

    collection_name = "scheduling_events"

    async def ensure_indexes(self) -> None:
        """Serve the three questions this trail actually gets asked:
        what happened to this slot, what happened to this patient's
        appointment, and what happened on this doctor's calendar. Plus a
        long-retention TTL on `at` -- a pure delete, so (unlike slot
        cleanup) this one genuinely works the same under `mongomock` and
        in production."""
        await self.collection.create_index([("slot_id", 1), ("at", -1)])
        await self.collection.create_index([("session_id", 1), ("at", -1)], sparse=True)
        await self.collection.create_index([("physician", 1), ("at", -1)])

    async def ensure_retention_index(self, retention_days: int) -> None:
        await self.collection.create_index(
            "at", expireAfterSeconds=retention_days * 86_400, name="scheduling_events_ttl"
        )

    async def record(self, event: SchedulingEvent) -> None:
        """Insert one event. Never raises -- callers must swallow-and-log,
        matching the exact contract this codebase already uses for
        `MongoTranscriptWriter` and Calendar delivery: an audit record for
        a write that already succeeded must never be allowed to undo it."""
        await self.collection.insert_one(event.model_dump())
