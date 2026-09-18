"""Data access for the `doctors` collection.

Read-mostly: looked up once per booking (`mock_booking`), once per
calendar-delivery attempt (`SessionService.attach_report`,
`VideoService.attach_recording`), and listed by the booking UI's provider
picker. Never touched mid-conversation.
"""

from hashlib import sha1

import structlog
from pymongo import ReadPreference

from app.core.constants import SymptomCategory
from app.models.doctor import Doctor, DoctorGoogleGrant, slugify_doctor_name
from app.repositories.base import MongoRepository

log = structlog.get_logger(__name__)

_NO_MONGO_ID = {"_id": 0}


class DoctorRepository(MongoRepository):
    """CRUD for the doctor -> calendar/grant record."""

    collection_name = "doctors"

    read_preference = ReadPreference.PRIMARY_PREFERRED
    """Reference data, so a booking page can still load without a primary.

    This is the one collection here that is genuinely read-mostly -- it
    changes when a doctor is seeded or registers, not during a visit. When
    a primary exists this behaves exactly like `PRIMARY`, so a doctor who
    has just registered still sees themselves; it diverges only when there
    is no primary at all, and in that window there are no recent writes to
    be stale about anyway, because writes are failing too.

    That window is not hypothetical. Observed against Atlas over a degraded
    link: two secondaries healthy, the primary unreachable behind a TLS
    handshake that would not complete, and `/booking/providers` failing
    outright for want of data that either secondary was holding.
    """

    async def ensure_indexes(self) -> None:
        """One document per doctor name and per doctor id, enforced rather than assumed.

        `doctor_id` is indexed separately from `name` because the booking UI
        looks a doctor up by id on every availability request.

        Backfills before indexing, deliberately: doctors seeded before
        `doctor_id` existed have no such field, and Mongo counts every one of
        those as the same `null` key -- so building the unique index first
        fails outright and the app refuses to start.
        """
        await self.collection.create_index("name", unique=True)
        await self._backfill_doctor_ids()
        await self.collection.create_index("doctor_id", unique=True)
        await self.collection.create_index("categories")

    async def _backfill_doctor_ids(self) -> None:
        """Give every pre-existing doctor a stable id derived from their name.

        Collisions are possible in principle (two distinct names can slugify
        alike, e.g. "Dr. Test" and "Dr Test"), so a taken slug gets a short
        deterministic suffix rather than losing the unique index.
        """
        taken = {
            doc["doctor_id"]
            async for doc in self.collection.find(
                {"doctor_id": {"$type": "string", "$ne": ""}}, {"_id": 0, "doctor_id": 1}
            )
        }

        cursor = self.collection.find(
            {"$or": [{"doctor_id": {"$exists": False}}, {"doctor_id": None}, {"doctor_id": ""}]},
            {"_id": 1, "name": 1},
        )
        async for doc in cursor:
            doctor_id = slugify_doctor_name(doc["name"])
            if doctor_id in taken:
                suffix = sha1(doc["name"].encode(), usedforsecurity=False).hexdigest()[:6]
                doctor_id = f"{doctor_id}-{suffix}"
            taken.add(doctor_id)

            await self.collection.update_one(
                {"_id": doc["_id"]}, {"$set": {"doctor_id": doctor_id}}
            )
            log.info("backfilled_doctor_id", doctor_id=doctor_id)

    async def get_by_name(self, name: str) -> Doctor | None:
        """Fetch one doctor by exact name match, or None if unknown."""
        doc = await self.collection.find_one({"name": name}, _NO_MONGO_ID)
        return Doctor(**doc) if doc else None

    async def get_by_id(self, doctor_id: str) -> Doctor | None:
        """Fetch one doctor by their stable booking id, or None if unknown."""
        doc = await self.collection.find_one({"doctor_id": doctor_id}, _NO_MONGO_ID)
        return Doctor(**doc) if doc else None

    async def get_by_google_email(self, email: str) -> Doctor | None:
        """Fetch the doctor who connected this Google account, if any.

        Used to make re-registration an update of the existing grant rather
        than a second doctor record for the same person.
        """
        doc = await self.collection.find_one({"google_grant.email": email}, _NO_MONGO_ID)
        return Doctor(**doc) if doc else None

    async def list_for_category(self, category: SymptomCategory | None) -> list[Doctor]:
        """All doctors who accept `category`, name-ordered; all doctors when it is None.

        A doctor with no `categories` at all is unrestricted and always
        included -- see the field's docstring for why.
        """
        query: dict[str, object] = {}
        if category is not None:
            # `$size: 0` matches an empty array but NOT a missing field, so a
            # doctor seeded before `categories` existed needs its own clause
            # or they silently vanish from every filtered booking search.
            query = {
                "$or": [
                    {"categories": category.value},
                    {"categories": {"$size": 0}},
                    {"categories": {"$exists": False}},
                ]
            }

        cursor = self.collection.find(query, _NO_MONGO_ID).sort("name", 1)
        return [Doctor(**doc) async for doc in cursor]

    async def upsert(self, doctor: Doctor) -> None:
        """Insert or replace one doctor's record.

        Idempotent by name, so re-seeding after editing calendar ids updates
        in place rather than accumulating duplicates. Deliberately does not
        write `google_grant` when the incoming doctor has none, so re-seeding
        cannot silently revoke a doctor's own Google authorization.
        """
        fields = doctor.model_dump(exclude_none=True)
        fields.pop("google_grant", None)
        await self.collection.update_one({"name": doctor.name}, {"$set": fields}, upsert=True)

    async def set_google_grant(self, doctor_id: str, grant: DoctorGoogleGrant) -> None:
        """Record the doctor's own Google authorization and point their calendar at it.

        The granted account's email is also their primary calendar id, so
        connecting Google replaces any placeholder `google_calendar_id` that
        was seeded before the doctor registered.
        """
        await self.collection.update_one(
            {"doctor_id": doctor_id},
            {
                "$set": {
                    "google_grant": grant.model_dump(),
                    "google_calendar_id": grant.email,
                }
            },
        )

    async def register(self, doctor: Doctor) -> None:
        """Create or update a doctor from the self-registration flow.

        Unlike `upsert`, this one does write `google_grant`: it is the flow
        that produces the grant in the first place.
        """
        await self.collection.update_one(
            {"doctor_id": doctor.doctor_id},
            {"$set": doctor.model_dump()},
            upsert=True,
        )
