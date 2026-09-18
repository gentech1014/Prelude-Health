"""Shared repository base."""

from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorDatabase
from pymongo import ReadPreference
from pymongo.read_preferences import _ServerMode


class MongoRepository:
    """Base class binding a repository to one collection on the shared database."""

    collection_name: str

    read_preference: _ServerMode = ReadPreference.PRIMARY
    """Where this collection's reads may be served from.

    `PRIMARY` for everything by default, and deliberately so: most
    collections here are read back moments after being written -- a session
    document is written and re-read several times during one call -- and a
    read that lands on a secondary that has not caught up would return a
    session missing the consent that was just recorded. Replication lag is
    normally milliseconds, but "normally" is not a property to build consent
    checks on.

    A repository whose data is reference material rather than live state
    overrides this. See `DoctorRepository`.
    """

    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._db = db

    @property
    def collection(self) -> AsyncIOMotorCollection:
        return self._db.get_collection(self.collection_name, read_preference=self.read_preference)
