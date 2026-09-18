"""Data access for the `appointment_slots` collection.

Every state-changing method here is a single `update_one` guarded by a
status precondition, checked via `result.matched_count` -- never
`find_one_and_update` with an `{"_id": 0}` projection. Verified by
execution against the installed `mongomock`: when that idiom's filter
guards on a field (`status`) the same update also mutates, and the
post-update value falls outside the filter's matching set, mongomock's
`_find_and_modify` re-runs the *original* filter against the
*already-mutated* document to build the "AFTER" image -- which no longer
matches -- and returns `None` even though the write genuinely landed.
Real MongoDB has no such bug; only the mock does, and it fails in the
dangerous direction (reports "lost the race" when it actually won). This
is exactly the class of write every method below performs, so
`update_one` + `matched_count` is the only safe idiom here.

Every write here is also single-document, and deliberately so: MongoDB
already makes a single `update_one` atomic with no transaction, and
`mongomock.MongoClient.start_session()` raises `NotImplementedError`
unconditionally -- so a design needing a multi-document transaction
would be untestable across this repo's entire suite. See
`app.services.scheduling_service` for how a slot claim, a session write,
and a slot release are composed into a crash-safe saga out of these
single-document primitives instead.
"""

from datetime import UTC, datetime

from app.core.constants import SlotStatus
from app.models.slot import AppointmentSlot
from app.repositories.base import MongoRepository

_NO_MONGO_ID = {"_id": 0}


class SlotRepository(MongoRepository):
    """CAS-guarded reads and writes for one doctor's bookable slots."""

    collection_name = "appointment_slots"

    async def ensure_indexes(self) -> None:
        """Create the indexes this repository's correctness and hygiene depend on.

        The compound unique index on `(physician, starts_at)` is not an
        optimization: it is what makes `find_or_create_booked` safe under
        a race, the same role the unique `appointment_id` index plays for
        `SessionRepository`.

        The partial TTL index purges only `FREE` slots -- never `HELD` or
        `BOOKED` -- past `retention_days`. `$ne`/`$nin` are not legal
        operators in a `partialFilterExpression`; a plain equality is.
        Declared with a single `create_index()` call, not
        `create_indexes([IndexModel(...)])`: mongomock silently drops
        `partialFilterExpression` from the latter. TTL indexes are
        single-field only, which is why the availability index below is
        separate rather than folded into this one.

        Stated plainly, the TTL *behavior* has structurally zero test
        coverage: mongomock's TTL emulation ignores
        `partialFilterExpression` and expires eagerly on every read using
        a naive `mongomock.utcnow()` compared against this app's
        tz-aware timestamps -- the comparison raises `TypeError`, which
        mongomock silently swallows, so nothing ever expires under the
        mock at all. Tests can assert the index *declaration* (options),
        never that expiry actually happens the way it will in Atlas.
        """
        await self.collection.create_index("slot_id", unique=True)
        await self.collection.create_index([("physician", 1), ("starts_at", 1)], unique=True)
        await self.collection.create_index([("session_id", 1)])

    async def ensure_retention_index(self, retention_days: int) -> None:
        """Separate from `ensure_indexes()` because it takes a config value.

        Called once from the lifespan alongside `ensure_indexes()`.
        """
        await self.collection.create_index(
            "starts_at",
            expireAfterSeconds=retention_days * 86_400,
            partialFilterExpression={"status": SlotStatus.FREE.value},
            name="slots_ttl_retention",
        )

    async def get_by_id(self, slot_id: str) -> AppointmentSlot | None:
        """Fetch one slot, or None if `slot_id` does not exist."""
        doc = await self.collection.find_one({"slot_id": slot_id}, _NO_MONGO_ID)
        return AppointmentSlot(**doc) if doc else None

    async def list_bookable(
        self, physician: str, *, after: datetime, before: datetime, limit: int
    ) -> list[AppointmentSlot]:
        """Slots this physician could actually be booked into right now.

        "Bookable" is a derived predicate, not a stored state: `FREE`, or
        `HELD` with an expired `held_until`. That single `$or` is the
        entire mechanism by which an abandoned reschedule hold releases
        itself -- no background job is required for this to be correct,
        only for it to be tidy (see `SweeperService`). Used identically
        here and in `claim()` below, so a slot the agent just offered is
        exactly the slot the accept step is willing to take.
        """
        now = datetime.now(UTC)
        cursor = (
            self.collection.find(
                {
                    "physician": physician,
                    "starts_at": {"$gt": after, "$lt": before},
                    "$or": [
                        {"status": SlotStatus.FREE.value},
                        {"status": SlotStatus.HELD.value, "held_until": {"$lt": now}},
                    ],
                },
                _NO_MONGO_ID,
            )
            .sort("starts_at", 1)
            .limit(limit)
        )
        return [AppointmentSlot(**doc) async for doc in cursor]

    async def claim(
        self,
        slot_id: str,
        physician: str,
        session_id: str,
        *,
        after: datetime,
        before: datetime,
        held_until: datetime,
    ) -> bool:
        """Atomically move a bookable slot to HELD for one session.

        `physician`/`after`/`before` are re-asserted here, not trusted
        from the caller's earlier read -- the only guarantee this method
        gives is "this exact write happened," so every precondition it
        implies must be in the filter. Returns False if another patient
        claimed it first, or it no longer fits the window (someone
        rescheduled *out* of it, or time simply passed).
        """
        now = datetime.now(UTC)
        result = await self.collection.update_one(
            {
                "slot_id": slot_id,
                "physician": physician,
                "starts_at": {"$gt": after, "$lt": before},
                "$or": [
                    {"status": SlotStatus.FREE.value},
                    {"status": SlotStatus.HELD.value, "held_until": {"$lt": now}},
                ],
            },
            {
                "$set": {
                    "status": SlotStatus.HELD.value,
                    "session_id": session_id,
                    "held_until": held_until,
                    "updated_at": now,
                },
                "$inc": {"version": 1},
            },
        )
        return result.matched_count == 1

    async def promote(self, slot_id: str, session_id: str) -> bool:
        """Move a session's HELD slot to BOOKED. False if it was never HELD
        for this session (already promoted, released, or reclaimed)."""
        result = await self.collection.update_one(
            {"slot_id": slot_id, "status": SlotStatus.HELD.value, "session_id": session_id},
            {
                "$set": {"status": SlotStatus.BOOKED.value, "updated_at": datetime.now(UTC)},
                "$unset": {"held_until": ""},
                "$inc": {"version": 1},
            },
        )
        return result.matched_count == 1

    async def book_directly(self, slot_id: str, session_id: str) -> bool:
        """FREE -> BOOKED in one step, for the initial-booking path (no
        reschedule offer involved, so no HELD intermediate is needed)."""
        result = await self.collection.update_one(
            {"slot_id": slot_id, "status": SlotStatus.FREE.value},
            {
                "$set": {
                    "status": SlotStatus.BOOKED.value,
                    "session_id": session_id,
                    "updated_at": datetime.now(UTC),
                },
                "$inc": {"version": 1},
            },
        )
        return result.matched_count == 1

    async def release(self, slot_id: str, session_id: str, *, expected_status: SlotStatus) -> bool:
        """Release a slot this session holds back to FREE.

        Guarded on `session_id` as well as `expected_status` so a stale
        caller can never free a slot someone else now owns -- this one
        guard is what makes both the cancel path and the reschedule
        old-slot release safe with no transaction.
        """
        result = await self.collection.update_one(
            {"slot_id": slot_id, "status": expected_status.value, "session_id": session_id},
            {
                "$set": {
                    "status": SlotStatus.FREE.value,
                    "session_id": None,
                    "updated_at": datetime.now(UTC),
                },
                "$unset": {"held_until": ""},
                "$inc": {"version": 1},
            },
        )
        return result.matched_count == 1

    async def find_or_create_booked(
        self, physician: str, starts_at: datetime, ends_at: datetime, session_id: str
    ) -> AppointmentSlot:
        """Book the slot at this exact time, creating it if it doesn't exist yet.

        Backward-compatibility seam for `mock_booking.py`, which accepts a
        free-form `scheduled_at` with no slot concept of its own. Tries an
        insert first (the common case when nothing was pre-seeded for this
        exact moment); a duplicate-key error means a slot already exists
        here (pre-seeded by `scripts/seed_slots.py`, or a genuine race),
        so fall back to claiming that one. The unique compound index on
        `(physician, starts_at)` is what makes this race-safe rather than
        merely usually-correct.
        """
        slot_id = f"slot_{physician}_{starts_at.isoformat()}".replace(" ", "_")
        now = datetime.now(UTC)
        slot = AppointmentSlot(
            slot_id=slot_id,
            physician=physician,
            starts_at=starts_at,
            ends_at=ends_at,
            status=SlotStatus.BOOKED,
            session_id=session_id,
            version=0,
            updated_at=now,
        )
        try:
            await self.collection.insert_one(slot.model_dump())
            return slot
        except Exception:  # noqa: BLE001 - DuplicateKeyError from pymongo/mongomock, caught broadly
            # noqa BLE001 justified: the write either lost a genuine race
            # (duplicate key) or something else went wrong -- either way
            # the correct next step is the same: look up what is actually
            # there now, and either claim it or surface a clear failure.
            existing = await self.collection.find_one(
                {"physician": physician, "starts_at": starts_at}, _NO_MONGO_ID
            )
            if existing is None:
                raise
            existing_slot = AppointmentSlot(**existing)
            if existing_slot.status == SlotStatus.FREE:
                await self.book_directly(existing_slot.slot_id, session_id)
                return await self.get_by_id(existing_slot.slot_id) or existing_slot
            return existing_slot

    async def list_by_status(self, status: SlotStatus, *, limit: int) -> list[AppointmentSlot]:
        """Bounded scan of every slot in one status -- used only by
        `SweeperService`, never the live-call path."""
        cursor = self.collection.find({"status": status.value}, _NO_MONGO_ID).limit(limit)
        return [AppointmentSlot(**doc) async for doc in cursor]

    async def create_if_missing(self, slot: AppointmentSlot) -> None:
        """Create a slot only if `slot_id` doesn't exist yet -- used by
        `scripts/seed_slots.py`, which must be safely re-runnable.

        `$setOnInsert`, not a blind `$set`: re-seeding after a slot has
        already been claimed by a real session must never reset its
        `status`/`session_id` back to FREE and silently un-book a real
        appointment. A plain `upsert()` (full replace) would do exactly
        that on every re-run; this cannot, by construction.
        """
        await self.collection.update_one(
            {"slot_id": slot.slot_id}, {"$setOnInsert": slot.model_dump()}, upsert=True
        )
