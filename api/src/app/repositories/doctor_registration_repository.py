"""Pending doctor-registration records, keyed by their OAuth `state`.

Google's consent flow is a redirect round-trip: the doctor leaves this API
before their details have anywhere to live, and comes back with nothing but
a `code` and whatever `state` we sent. This collection is that missing
middle -- it holds the details the doctor typed, addressed by an
unguessable `state`, until the callback can claim them.

`state` is also the flow's CSRF defense, which is why claiming is
destructive: a `state` works exactly once, so a callback URL cannot be
replayed to re-register a doctor. Same reasoning as
`app.repositories.ws_ticket_repository`, and a real collection for the same
reason -- an in-process dict would silently stop enforcing single-use under
more than one worker.
"""

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from app.core.constants import CareModality, SymptomCategory
from app.repositories.base import MongoRepository


class PendingDoctorRegistration(BaseModel):
    """The doctor's own details, held between consent redirect and callback."""

    name: str
    credential: str | None
    categories: list[SymptomCategory]
    modality: CareModality


class DoctorRegistrationRepository(MongoRepository):
    """Stores and claims pending registrations by OAuth state."""

    collection_name = "doctor_registrations"

    async def ensure_indexes(self) -> None:
        """Unique on state, with Mongo expiring abandoned registrations itself.

        A doctor who closes the Google consent screen leaves a row behind;
        the TTL index is what stops those accumulating.
        """
        await self.collection.create_index("state", unique=True)
        await self.collection.create_index("expires_at", expireAfterSeconds=0)

    async def create(
        self, state: str, registration: PendingDoctorRegistration, ttl_seconds: int
    ) -> None:
        """Park one registration under `state` until the OAuth callback claims it."""
        await self.collection.insert_one(
            {
                "state": state,
                **registration.model_dump(),
                "expires_at": datetime.now(UTC) + timedelta(seconds=ttl_seconds),
            }
        )

    async def claim(self, state: str) -> PendingDoctorRegistration | None:
        """Consume `state` and return its registration, or None if unknown/expired/spent.

        A find-and-delete rather than a read: the delete is what makes the
        state single-use, and doing both in one operation leaves no window
        where two concurrent callbacks could each claim the same one.
        """
        doc = await self.collection.find_one_and_delete({"state": state})
        if doc is None:
            return None
        return PendingDoctorRegistration(
            name=doc["name"],
            credential=doc.get("credential"),
            categories=doc.get("categories", []),
            modality=doc.get("modality", CareModality.IN_PERSON),
        )
