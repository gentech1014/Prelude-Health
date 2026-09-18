"""Data access for the `prescreening_question_bank` collection.

One document per (category, question). Read in bulk at boot into
`app.services.question_bank_service`, so the live call never waits on a
query mid-conversation, and written once per call -- after the patient has
hung up -- when what was actually asked is merged back in.

The old shape was one document per category holding `questions: list[str]`,
under the collection name `questions_bank`. It is not read any more: the
call no longer picks a question out of a fixed list, so a list of strings
has nowhere to record how often one has earned its place. Re-run
`make seed-db` once to populate the new collection.

Writes are one `update_one` per question rather than a single `bulk_write`.
There are at most ten of them, they happen after the patient has hung up,
and each is independently atomic either way -- so batching bought nothing
here except a call the test suite's Mongo stand-in cannot proxy.
"""

from datetime import UTC, datetime

from app.core.constants import PrescreeningCategory
from app.models.question_bank import BankQuestion
from app.repositories.base import MongoRepository

_NO_MONGO_ID = {"_id": 0}


class QuestionBankRepository(MongoRepository):
    """Read and accumulate the per-category prescreening question bank."""

    collection_name = "prescreening_question_bank"

    async def ensure_indexes(self) -> None:
        """Ordered by how often a question has been asked, within its category.

        Which is exactly how the reference set for a new call is drawn, so
        the index matches the only query that matters here. Uniqueness is
        already guaranteed by `_id` being `category::key`.
        """
        await self.collection.create_index([("category", 1), ("asked_count", -1)])

    async def get_all(self) -> list[BankQuestion]:
        """Every question in the bank, for the boot-time in-memory preload."""
        cursor = self.collection.find({}, _NO_MONGO_ID)
        return [BankQuestion(**doc) async for doc in cursor]

    async def get_by_category(self, category: PrescreeningCategory) -> list[BankQuestion]:
        """One category's questions, most-asked first."""
        cursor = self.collection.find({"category": category.value}, _NO_MONGO_ID).sort(
            "asked_count", -1
        )
        return [BankQuestion(**doc) async for doc in cursor]

    async def seed(self, questions: list[BankQuestion]) -> int:
        """Insert a hand-written starting corpus, without disturbing what calls have learned.

        Every field goes in under `$setOnInsert`, so re-running the seed is a
        no-op for anything already there: a question real calls have since
        asked forty times keeps its count, and one an editor has reworded is
        not silently reverted.

        Seeds land at `asked_count: 0` on purpose. They are the cold-start
        floor for a reason, and the reference set is cut by count -- so as
        soon as real calls start asking real questions for that reason,
        those outrank the hand-written guesses, which is the right way round.

        Returns how many were actually new.
        """
        inserted = 0
        for question in questions:
            result = await self.collection.update_one(
                {"_id": question.document_id},
                {
                    "$setOnInsert": {
                        "category": question.category.value,
                        "key": question.key,
                        "text": question.text,
                        "asked_count": 0,
                        "source": question.source.value,
                        "first_asked_at": None,
                        "last_asked_at": None,
                    }
                },
                upsert=True,
            )
            inserted += 1 if result.upserted_id is not None else 0
        return inserted

    async def record_asked(self, questions: list[BankQuestion]) -> int:
        """Merge questions that were actually asked into the bank.

        Upsert per question, keyed by `category::normalized-text`, so asking
        the same thing again increments a counter instead of adding a near
        duplicate -- and two calls screening the same reason at once both
        count, which a read-modify-write on a per-category list could not
        promise.

        `$setOnInsert` holds `first_asked_at` and `source`, so a question
        first written by hand as a seed is never relabelled as generated
        just because the agent later happened to ask it.

        Returns the number of documents written. Callers treat a failure as
        non-fatal: losing this costs the *next* patient a slightly thinner
        reference set, and the call it came from is already over.
        """
        now = datetime.now(UTC)
        written = 0
        for question in questions:
            result = await self.collection.update_one(
                {"_id": question.document_id},
                {
                    "$inc": {"asked_count": question.asked_count},
                    "$set": {"text": question.text, "last_asked_at": now},
                    "$setOnInsert": {
                        "category": question.category.value,
                        "key": question.key,
                        "source": question.source.value,
                        "first_asked_at": now,
                    },
                },
                upsert=True,
            )
            written += 1 if (result.upserted_id is not None or result.modified_count) else 0
        return written
