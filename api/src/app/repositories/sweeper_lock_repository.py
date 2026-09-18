"""Data access for the `sweeper_locks` collection -- one document, one lease.

Gives multiple app replicas mutual exclusion over the periodic sweep with
no new dependency: a single-document `update_one` is already atomic, so
the same guard-and-check idiom used everywhere else in this feature does
the job. No Redis, no leader election beyond "whoever's `update_one`
matched first."
"""

from datetime import UTC, datetime, timedelta

from app.repositories.base import MongoRepository

_LEASE_ID = "slot_sweeper"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class SweeperLockRepository(MongoRepository):
    """A single lease document, contended by every running replica."""

    collection_name = "sweeper_locks"

    async def ensure_seeded(self) -> None:
        """Create the one lease document if it doesn't exist yet.

        `$setOnInsert` so a replica that starts second does not reset a
        lease another replica already holds.
        """
        await self.collection.update_one(
            {"_id": _LEASE_ID},
            {"$setOnInsert": {"lease_until": _EPOCH, "owner": None}},
            upsert=True,
        )

    async def try_acquire(self, owner: str, lease_seconds: int) -> bool:
        """Attempt to take the lease. False means another replica holds it --
        skip this tick, don't retry, don't error.

        Deliberately `upsert=False`: an `upsert=True` acquire would surface
        the losing side as a `DuplicateKeyError` on `_id`, which
        `mongomock` cannot reproduce and which reads like a bug rather
        than an expected outcome. With `ensure_seeded()` guaranteeing the
        document already exists, the losing path here is a plain,
        testable `matched_count == 0`.
        """
        now = datetime.now(UTC)
        result = await self.collection.update_one(
            {"_id": _LEASE_ID, "lease_until": {"$lt": now}},
            {"$set": {"lease_until": now + timedelta(seconds=lease_seconds), "owner": owner}},
            upsert=False,
        )
        return result.matched_count == 1
