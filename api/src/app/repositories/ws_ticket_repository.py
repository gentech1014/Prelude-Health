"""Spend-once ledger for WebSocket tickets.

A signed, expiring ticket proves *who* minted it but not *how many times it
has been used* -- signatures are stateless, so the same ticket replays
forever until it expires. This collection is the state that makes a ticket
single-use: spending one is an insert, and the unique index turns a replay
into a duplicate-key error rather than a second accepted connection.

Deliberately a real collection rather than an in-process set: the app is a
single uvicorn process today, but an in-memory ledger would silently stop
enforcing anything the moment it is run with more than one worker -- the
worst kind of security regression, because nothing fails visibly.
"""

from datetime import UTC, datetime, timedelta

from app.repositories.base import MongoRepository


class WsTicketRepository(MongoRepository):
    """Records which WebSocket ticket nonces have already been spent."""

    collection_name = "ws_tickets"

    async def ensure_indexes(self) -> None:
        """Unique on nonce, with Mongo expiring the rows on its own.

        The TTL index is what keeps this collection from growing without
        bound: a spent nonce only needs to outlive the ticket that carried
        it, and the ticket's own expiry check rejects it long before then.
        """
        await self.collection.create_index("nonce", unique=True)
        await self.collection.create_index("expires_at", expireAfterSeconds=0)

    async def spend(self, session_id: str, nonce: str, ttl_seconds: int) -> bool:
        """Claim a nonce. Returns True on the first spend, False on a replay.

        An upsert rather than a plain insert, because the *insert* form
        makes replay protection depend entirely on the unique index being
        present: against a store that does not enforce it, the duplicate
        simply succeeds and every ticket becomes infinitely replayable with
        nothing failing visibly. `upserted_id` is set only on the call that
        actually created the row, so the check holds either way -- the
        index above then narrows the remaining concurrent-insert race,
        instead of being the whole mechanism.

        `ttl_seconds` should be the ticket's own lifetime plus a margin:
        the row only needs to outlive the signature it guards.
        """
        result = await self.collection.update_one(
            {"nonce": nonce},
            {
                "$setOnInsert": {
                    "nonce": nonce,
                    "session_id": session_id,
                    "expires_at": datetime.now(UTC) + timedelta(seconds=ttl_seconds),
                }
            },
            upsert=True,
        )
        return result.upserted_id is not None
