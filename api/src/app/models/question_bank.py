"""The prescreening question bank -- what has been asked, per appointment reason.

This is no longer a fixed script the call reads from. The live agent writes
its own questions on the spot, against the coverage brief for the reason the
patient actually booked under (`PRESCREENING_CATEGORY_BRIEFS`), and every
question it asks is folded back in here afterwards, keyed by that category.

So the bank has two jobs, and only the second one is new:

- *Reference*, at the start of a screening: the questions that have proved
  worth asking for this reason before are handed to the model as material it
  MAY draw on. Not a script, not an allowlist -- the model is explicitly told
  it is expected to write better ones for the patient in front of it.
- *Accumulation*, at the end of a call: what was actually asked is merged in,
  deduplicated by normalized text, with a count. A reason nobody has booked
  yet starts empty and fills itself from real calls.

One document per (category, question) rather than one document per category
holding a list. That is what makes the merge a single atomic `$inc` upsert
instead of a read-modify-write, which two calls screening the same condition
at the same time would otherwise race on and lose a count to.
"""

import re
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.core.constants import PrescreeningCategory

_PUNCTUATION = re.compile(r"[^a-z0-9\s]+")
_WHITESPACE = re.compile(r"\s+")
_LEADING_FILLER = re.compile(
    r"^(?:"
    r"and |so |ok |okay |right |now |just |"
    r"can you tell me |could you tell me |can you describe |"
    r"could you describe |can i ask |could i ask |i want to ask |"
    r"i would like to know |id like to know |tell me |"
    r"do you mind telling me |would you mind telling me |"
    r"can you |could you |do you |have you |are you |is there |"
    r"has there been |what about "
    r")+"
)
"""Openers a voice model varies freely between turns.

"Can you tell me how long this has been going on" and "How long has this
been going on" are the same question, and letting both into the bank as
separate entries is how a reusable corpus turns into a pile of near
duplicates within a few dozen calls."""


def normalize_question(text: str) -> str:
    """The dedup key for one question: what it asks, stripped of how it was phrased.

    Lossy on purpose, and only ever used as a key -- the question the
    patient actually heard is stored verbatim alongside it, and that is
    what reaches the physician's report.
    """
    lowered = _PUNCTUATION.sub(" ", text.strip().lower())
    collapsed = _WHITESPACE.sub(" ", lowered).strip()
    stripped = _LEADING_FILLER.sub("", collapsed).strip()
    return stripped or collapsed


_GRAMMAR_WORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "this",
        "that",
        "these",
        "those",
        "do",
        "does",
        "did",
        "doing",
        "done",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "am",
        "has",
        "have",
        "had",
        "having",
        "can",
        "could",
        "will",
        "would",
        "shall",
        "should",
        "may",
        "might",
        "must",
        "i",
        "me",
        "my",
        "mine",
        "you",
        "your",
        "yours",
        "it",
        "its",
        "they",
        "them",
        "their",
        "we",
        "us",
        "our",
        "of",
        "to",
        "at",
        "in",
        "on",
        "for",
        "and",
        "or",
        "with",
        "about",
        "from",
        "by",
        "as",
        "any",
        "some",
        "there",
        "here",
        "just",
        "really",
        "quite",
        "ever",
        "also",
        "please",
    ]
)
"""Words that carry no part of what a question is asking.

Interrogatives are deliberately absent: "when did it start" and "where did
it start" reduce to the same thing without them, and a guard that treats
those as one question would silently drop half of what a screening needs
to establish."""


def question_fingerprint(text: str) -> frozenset[str]:
    """What a question asks about, independent of word order and phrasing.

    Used only to decide whether this call has already asked something --
    never as a storage key, where losing word order would merge questions
    that deserve separate entries.

    Order-independent because a model writing its own questions varies the
    order freely between turns: "how long has this been going on" and "can
    you tell me how long this has been going on" are the same question to a
    patient, and matching them on normalized text alone does not catch it.
    """
    words = normalize_question(text).split()
    content = frozenset(word for word in words if word not in _GRAMMAR_WORDS)
    # Too little signal to be safe: "any nausea?" and "any pain?" both
    # reduce to one word, and one word is not enough to call two questions
    # the same. Fall back to the whole normalized phrase.
    return content if len(content) >= 2 else frozenset(words)


class QuestionSource(StrEnum):
    """Where a bank entry came from."""

    SEED = "seed"
    """Written by hand in `scripts/seed_question_bank.py`, as a starting corpus
    for the five conditions that already had one. Never required."""

    GENERATED = "generated"
    """Written by the live agent during a real call, for a real patient."""


class BankQuestion(BaseModel):
    """One question in the bank, with how often it has actually been asked.

    `asked_count` is the whole point of storing these individually: it is
    what lets the reference set handed to the next call be ordered by what
    has repeatedly proved worth asking for this reason, rather than by
    whatever order someone typed them in.
    """

    category: PrescreeningCategory
    key: str = Field(description="`normalize_question(text)` -- the dedup identity")
    text: str = Field(description="The question as it was actually asked, verbatim")
    asked_count: int = 0
    source: QuestionSource = QuestionSource.GENERATED
    first_asked_at: datetime | None = None
    last_asked_at: datetime | None = None

    @classmethod
    def build(
        cls,
        category: PrescreeningCategory,
        text: str,
        *,
        source: QuestionSource = QuestionSource.GENERATED,
    ) -> "BankQuestion":
        now = datetime.now(UTC)
        return cls(
            category=category,
            key=normalize_question(text),
            text=text.strip(),
            asked_count=1,
            source=source,
            first_asked_at=now,
            last_asked_at=now,
        )

    @property
    def document_id(self) -> str:
        """Stable `_id`, so re-asking the same question increments rather than inserts."""
        return f"{self.category.value}::{self.key}"


class CategoryQuestions(BaseModel):
    """A whole category's questions, as the seed script and admin reads hand them around."""

    category: PrescreeningCategory
    questions: list[BankQuestion] = Field(default_factory=list)
