"""In-memory prescreening question bank: reference on the way in, accumulation on the way out.

Preloaded once from Mongo at process boot (via the FastAPI lifespan) so the
live call never makes a DB read mid-conversation -- that would add latency
a voice call cannot afford. Written back once per call, after the patient
has hung up, for the same reason.

The bank is no longer authoritative over what gets asked. The agent writes
its own questions against the coverage brief for the patient's appointment
reason; this supplies what has proved worth asking for that reason before,
as material the model may draw on, and records what it ended up asking so
the next patient with the same reason starts from a better place.
"""

from typing import TYPE_CHECKING

import structlog

from app.core.constants import PrescreeningCategory
from app.models.question_bank import BankQuestion, QuestionSource, normalize_question
from app.repositories.question_bank_repository import QuestionBankRepository

if TYPE_CHECKING:
    from app.services.moss_question_bank import MossQuestionBankClient

log = structlog.get_logger(__name__)

REFERENCE_QUESTION_LIMIT = 12
"""How many previously-asked questions one screening is shown.

Enough for the model to see the shape of a good screening for this reason,
short enough that it still reads as reference rather than as a script it is
being handed. The cut is by `asked_count`, so what survives is what has
repeatedly earned its place."""


class QuestionBankService:
    """Process-wide singleton holding `{category: questions}` in memory."""

    def __init__(
        self,
        repository: QuestionBankRepository,
        moss_client: "MossQuestionBankClient | None" = None,
    ) -> None:
        self._repository = repository
        self._cache: dict[PrescreeningCategory, list[BankQuestion]] = {}
        self._loaded = False
        # Optional and additive: None on any deployment that has not set
        # MOSS_PROJECT_ID/MOSS_PROJECT_KEY, which then behaves exactly as
        # this service always has.
        self._moss = moss_client

    async def preload(self) -> None:
        """Load the whole bank into memory. Call once at boot.

        Deliberately never fatal, unlike the version this replaces. That one
        refused to start the server if any category was unseeded, which was
        correct while the bank *was* the script -- an unseeded category then
        meant a call with no questions to ask. It is now a reference corpus:
        a category nobody has booked yet legitimately holds nothing, and
        refusing to boot over that would block exactly the two reasons
        (a routine checkup, and not knowing what is wrong) that have no
        hand-written starting set.
        """
        grouped: dict[PrescreeningCategory, list[BankQuestion]] = {}
        for question in await self._repository.get_all():
            grouped.setdefault(question.category, []).append(question)
        for questions in grouped.values():
            questions.sort(key=lambda question: question.asked_count, reverse=True)

        self._cache = grouped
        self._loaded = True

        empty = sorted(
            category.value for category in PrescreeningCategory if not grouped.get(category)
        )
        log.info(
            "question_bank_preloaded",
            categories=len(grouped),
            questions=sum(len(questions) for questions in grouped.values()),
            # Not a warning: an empty category is a reason nobody has been
            # screened for yet, which the first such call fixes by itself.
            without_reference_questions=empty,
        )

    def reference_questions(
        self, category: PrescreeningCategory, limit: int = REFERENCE_QUESTION_LIMIT
    ) -> list[BankQuestion]:
        """The most-asked questions for one appointment reason, as reference material.

        Returns an empty list for a category nothing has been recorded
        against, which is a normal state and not an error -- the agent
        writes the whole screening itself in that case, which it is
        instructed to be prepared for regardless.
        """
        return self._cache.get(category, [])[:limit]

    async def reference_questions_live(
        self,
        category: PrescreeningCategory,
        query_text: str,
        limit: int = REFERENCE_QUESTION_LIMIT,
    ) -> list[BankQuestion]:
        """Same job as `reference_questions`, semantic and live when Moss is configured.

        `query_text` is the extra signal the in-memory version cannot use --
        the appointment reason or what the patient has actually said -- so
        the reference set can react to *this* patient rather than only to
        the category. Falls back to the plain in-memory list, silently, on
        any Moss failure or when Moss is not configured at all: this call
        sits directly in the live voice path, and a reference-question
        lookup is never worth failing a call over.
        """
        if self._moss is None:
            return self.reference_questions(category, limit)
        try:
            return await self._moss.reference_questions(category, query_text, limit)
        except Exception:
            log.warning("moss_reference_lookup_failed", category=category.value)
            return self.reference_questions(category, limit)

    async def record_asked(self, category: PrescreeningCategory, texts: list[str]) -> int:
        """Fold the questions one call actually asked back into the bank.

        Collapses to one entry per distinct question first, so a question
        the agent asked and then re-asked after a mishearing contributes a
        single count -- `asked_count` is meant to say how many patients
        have been asked something, not how many times the agent said it.
        Then merges into Mongo with `$inc` and refreshes the in-memory copy,
        without which the next call in this process would still see the
        pre-call bank.

        Returns how many entries were written. Never raises: this runs
        after the patient has hung up, and losing it costs the next patient
        a slightly thinner reference set, nothing more.
        """
        merged: dict[str, BankQuestion] = {}
        for text in texts:
            cleaned = (text or "").strip()
            if not cleaned:
                continue
            key = normalize_question(cleaned)
            if not key or key in merged:
                continue
            merged[key] = BankQuestion.build(category, cleaned, source=QuestionSource.GENERATED)

        if not merged:
            return 0

        try:
            written = await self._repository.record_asked(list(merged.values()))
        except Exception:
            log.exception("question_bank_write_failed", category=category.value)
            return 0

        self._merge_into_cache(category, list(merged.values()))
        log.info(
            "question_bank_updated",
            category=category.value,
            # Counts only. Which questions a patient was asked is their
            # health information, and never reaches the log.
            written=written,
            bank_size=len(self._cache.get(category, [])),
        )
        return written

    def _merge_into_cache(
        self, category: PrescreeningCategory, questions: list[BankQuestion]
    ) -> None:
        """Apply the same merge to the in-memory copy that Mongo just applied."""
        current = {question.key: question for question in self._cache.get(category, [])}
        for question in questions:
            existing = current.get(question.key)
            if existing is None:
                current[question.key] = question
            else:
                existing.asked_count += question.asked_count
                existing.text = question.text
        self._cache[category] = sorted(
            current.values(), key=lambda question: question.asked_count, reverse=True
        )

    @property
    def is_loaded(self) -> bool:
        return self._loaded
