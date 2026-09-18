"""Live, semantic question-bank lookup via Moss -- optional, additive layer.

Entirely separate from `app.services.question_bank_service.QuestionBankService`,
which stays the source of truth (Mongo-backed accumulation, in-memory
preload) exactly as it was before this existed. This client only replaces
the *read* side, and only when `MOSS_PROJECT_ID`/`MOSS_PROJECT_KEY` are set --
a deployment that never configures them gets the old in-memory behavior,
unchanged.

Populate the index once via `scripts/seed_moss_index.py`, which pushes the
same corpus `seed_question_bank.py` writes to Mongo. This client only reads.
"""

import structlog
from moss import MossClient, QueryOptions

from app.core.config import Settings
from app.core.constants import PrescreeningCategory
from app.models.question_bank import BankQuestion, QuestionSource

log = structlog.get_logger(__name__)


class MossQuestionBankClient:
    """Thin wrapper: semantic top-k lookup, filtered by category."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = MossClient(settings.moss_project_id, settings.moss_project_key)
        self._index = settings.moss_question_index
        self._loaded = False

    async def ensure_loaded(self) -> None:
        """Load the index into Moss's serving layer once, at boot.

        Mirrors why `QuestionBankService.preload` exists at all: a live
        voice call cannot afford to pay a cold-load cost mid-conversation.
        """
        if self._loaded:
            return
        await self._client.load_index(self._index)
        self._loaded = True

    async def reference_questions(
        self, category: PrescreeningCategory, query_text: str, limit: int
    ) -> list[BankQuestion]:
        """Semantically closest reference questions for one category.

        `query_text` is what the patient's appointment reason / what they
        have said so far actually is -- unlike the old count-ranked list,
        this can react to it. Raises on a Moss failure; the caller decides
        whether and how to fall back to the in-memory bank.
        """
        results = await self._client.query(
            self._index,
            query_text,
            QueryOptions(
                top_k=limit,
                filter={"field": "category", "condition": {"$eq": category.value}},
            ),
        )
        return [
            BankQuestion.build(category, doc.text, source=QuestionSource.SEED)
            for doc in results.docs
        ]
