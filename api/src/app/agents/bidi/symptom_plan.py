"""The screening conversation's state for one call: what it is about, and what landed.

The symptom-story screen is the one screen with no fixed fields, and now
with no fixed questions either. What belongs on it depends entirely on the
appointment reason the patient booked under and on what they have said so
far, so the agent writes each question on the spot and this holds the
result: which reason is being screened, what was asked, and every answer in
the order it arrived.

What changed, and why: this used to hold a `planned` list -- one of five
seeded question sets, loaded up front, with the agent restricted to picking
questions out of it by id. That made every patient in a category hear the
same interview, and worse, made the *category* a forced choice among five,
so a routine checkup or an unplaceable complaint (23% of real bookings, and
the two reasons with no clinical set at all) got filed under whichever set
sounded closest to unexplained pain. Now the category is the reason they
actually booked under, the questions are written for the patient in front of
the agent, and the bank the old `planned` list came from is reference
material and an accumulation target rather than an allowlist.

`asked_texts` is what makes the accumulation work: every question this call
put on screen, flushed into the bank keyed by category once the call is over
(see `app.services.question_bank_service.QuestionBankService.record_asked`).

Pure state, deliberately: nothing here touches the socket. The context that
owns it publishes the frames, so this stays testable without a call.
"""

from dataclasses import dataclass, field

from app.core.constants import AnswerStatus, PrescreeningCategory, PresentationType
from app.models.question_bank import BankQuestion, question_fingerprint
from app.models.symptom_intake import SymptomAnswer

MIN_SYMPTOM_QUESTIONS = 7
"""Fewest questions a symptom-story screening must cover before the call can move on.

Product requirement: a screening that closes after two or three questions
does not give the doctor enough to work with. `navigate_to_screen` enforces
this -- the call cannot leave `symptom-story` until it is met."""

MAX_SYMPTOM_QUESTIONS = 10
"""Most questions a symptom-story screening may ask.

Product requirement, paired with `MIN_SYMPTOM_QUESTIONS`: enough headroom
for a real follow-up thread, not so much that the call drags. Enforced in
`ask_symptom_question`, which refuses a question once this is reached."""

MAX_SYMPTOM_CATEGORIES = 2
"""How many appointment reasons one call may screen for.

Two, not one: a patient booked for a blood-pressure review who then says
their real worry is a cough has two things worth screening, and forcing the
agent to pick one loses whichever it drops. Not unbounded, because a third
reason is no longer this visit -- it is a different appointment, and
screening it here buries what the patient actually came in for."""

MAX_QUESTIONS_PUT_ON_SCREEN = MAX_SYMPTOM_QUESTIONS + 4
"""Hard ceiling on questions *asked*, however few of them are answered.

`MAX_SYMPTOM_QUESTIONS` counts answers, which leaves a patient who will
not engage with the screening in a loop with no exit: every question they
deflect leaves `answered` unmoved, so the upper bound is never reached
while the lower one keeps refusing to let the call move on. Four questions
of headroom is enough to re-ask a couple that did not land and no more.
"""

MAX_QUESTION_LENGTH = 300
"""Cap on the question text shown on screen. A question, not a paragraph."""

MAX_ANSWER_LENGTH = 500
"""Cap on one displayed answer -- the untruncated words are in the transcript."""


@dataclass
class AskedQuestion:
    """One question this call wrote and put on the patient's screen."""

    id: str
    category: PrescreeningCategory
    text: str

    @property
    def fingerprint(self) -> frozenset[str]:
        return question_fingerprint(self.text)


@dataclass
class SymptomQuestionPlan:
    """Which appointment reason this call is screening, and everything asked so far."""

    categories: list[PrescreeningCategory] = field(default_factory=list)
    presentation: PresentationType | None = None
    """How the problem presented, which decides what is worth asking about it.

    The axis the screening had no way to express, and the reason a torn
    hamstring was asked whether it "comes and goes": `categories` says
    which body system, and for an injury the honest answer to that is
    "none of the seven", so it landed on `not_sure` and inherited a brief
    written for an unexplained symptom. See
    `app.core.constants.PresentationType`.

    One per call rather than one per category: a patient is telling one
    story, and when a second reason is screened alongside the first it is
    almost always the same kind of story about a different system.
    """

    reference: dict[PrescreeningCategory, list[BankQuestion]] = field(default_factory=dict)
    """Previously-asked questions per category, handed to the model as material.

    Never consulted by this class for validation. It exists so the tool can
    show the agent what has proved worth asking for this reason before; a
    question it writes is accepted whether or not it resembles anything in
    here."""

    asked: list[AskedQuestion] = field(default_factory=list)
    answered: list[SymptomAnswer] = field(default_factory=list)
    current: AskedQuestion | None = None

    @property
    def is_started(self) -> bool:
        """Whether an appointment reason has been confirmed and screening has begun."""
        return bool(self.categories)

    @property
    def primary_category(self) -> PrescreeningCategory | None:
        """The reason the screening is mainly about -- the first one confirmed."""
        return self.categories[0] if self.categories else None

    @property
    def at_question_max(self) -> bool:
        """Whether this screening has reached the most questions it may ask.

        Either bound ends it: enough answers collected, or enough questions
        put on screen regardless of how many came back. The second is what
        stops a patient who deflects everything from being asked forever --
        see `MAX_QUESTIONS_PUT_ON_SCREEN`.
        """
        return (
            len(self.answered) >= MAX_SYMPTOM_QUESTIONS
            or len(self.asked) >= MAX_QUESTIONS_PUT_ON_SCREEN
        )

    @property
    def substantive_count(self) -> int:
        """Answers where the patient actually told you something.

        Declines and "I don't know" are real answers and count toward the
        minimum -- the patient engaged, and the physician learns something
        either way -- but they are not coverage of the topic, so this is
        what the tool reports when it describes how the screening is
        actually going.
        """
        return sum(
            1
            for entry in self.answered
            if entry.status in (AnswerStatus.CONFIRMED, AnswerStatus.UNCERTAIN)
        )

    @property
    def below_question_min(self) -> bool:
        """Whether this screening still needs more questions before the call can move on."""
        return len(self.answered) < MIN_SYMPTOM_QUESTIONS

    @property
    def at_category_limit(self) -> bool:
        return len(self.categories) >= MAX_SYMPTOM_CATEGORIES

    def start(
        self,
        category: PrescreeningCategory,
        reference: list[BankQuestion],
        presentation: PresentationType | None = None,
    ) -> PrescreeningCategory:
        """Begin (or extend) screening for one appointment reason.

        Idempotent per category and additive across categories, so a second
        reason appends rather than replacing and a repeated confirmation
        changes nothing.

        `presentation` is set the first time it is supplied and then left
        alone. A second reason does not re-characterize the first: a
        patient who tore a muscle and also wants their blood pressure
        looked at is still telling you about an injury, and letting the
        later, vaguer call overwrite it would put the pattern questions
        back on the torn muscle.
        """
        if category not in self.categories:
            self.categories.append(category)
        self.reference[category] = reference
        if presentation is not None and self.presentation is None:
            self.presentation = presentation
        return category

    def answer_for(self, question_id: str) -> SymptomAnswer | None:
        return next((entry for entry in self.answered if entry.question_id == question_id), None)

    def already_asked(self, text: str) -> AskedQuestion | None:
        """Whether this call has put substantially this question on screen already.

        Matched on `question_fingerprint`, not on the text: the agent writes
        each question fresh, so the same question comes back with different
        filler and a different word order every time. Re-asking is the one
        failure a patient notices instantly, and there is no longer an id
        list colliding to catch it -- so this is where the check has to live.
        """
        fingerprint = question_fingerprint(text)
        if not fingerprint:
            return None
        return next((entry for entry in self.asked if entry.fingerprint == fingerprint), None)

    def match_asked(self, text: str) -> AskedQuestion | None:
        """The question this call asked that `text` refers to, if any.

        The same fingerprint match `already_asked` uses, and deliberately
        so: the agent describes a question it asked several turns ago in
        its own words, exactly as it would if it were about to re-ask it.
        One is a correction and one is a repeat, and they are recognized
        the same way.

        Question ids are never exposed to the model -- they are internal,
        and asking a speech model to carry one across ten turns would
        invent more mistakes than it prevented.
        """
        fingerprint = question_fingerprint(text)
        if not fingerprint:
            return None
        # Most recent first: if the same ground was covered twice, the
        # correction belongs to the later one.
        return next(
            (entry for entry in reversed(self.asked) if entry.fingerprint == fingerprint),
            None,
        )

    def ask(self, category: PrescreeningCategory, text: str) -> AskedQuestion:
        """Put one newly-written question on the patient's screen."""
        question = AskedQuestion(
            id=f"{category.value}-{len(self.asked) + 1:02d}",
            category=category,
            text=text.strip()[:MAX_QUESTION_LENGTH],
        )
        self.asked.append(question)
        self.current = question
        return question

    def clear_current(self) -> None:
        self.current = None

    def record(
        self,
        question_id: str,
        answer: str,
        *,
        clear_current: bool = True,
        status: AnswerStatus = AnswerStatus.CONFIRMED,
    ) -> SymptomAnswer | None:
        """Store what the patient said, and take the question off the screen.

        Returns None for a question this call never asked, rather than
        inventing one: an id the model made up must not become a line in
        the physician's report. Re-recording an answered question
        overwrites it in place -- a correction, not a second finding.

        `clear_current=False` is for an answer the patient *typed*: taking
        the question off their screen while they are still typing into it
        loses whatever they had not finished. The agent's own
        `record_symptom_answer` clears it, which is the point in the
        conversation where it is actually safe to.
        """
        question = next((entry for entry in self.asked if entry.id == question_id), None)
        if question is None:
            return None

        cleaned = answer.strip()[:MAX_ANSWER_LENGTH]
        if not cleaned:
            return None

        existing = self.answer_for(question_id)
        if existing is not None:
            existing.answer = cleaned
            existing.status = status
            recorded = existing
        else:
            recorded = SymptomAnswer(
                question_id=question.id,
                category=question.category,
                question=question.text,
                answer=cleaned,
                status=status,
            )
            self.answered.append(recorded)

        if clear_current and self.current is not None and self.current.id == question_id:
            self.clear_current()
        return recorded

    def asked_texts(self, category: PrescreeningCategory) -> list[str]:
        """The questions this call asked under one reason, for the bank flush.

        Only questions the patient actually *engaged with*: one the agent
        put on screen and then abandoned mid-turn is not evidence that it
        was worth asking, and letting those accumulate would fill the
        reference corpus with the model's own false starts.

        A declined or unanswerable question is excluded for the same
        reason, and it is a sharper one. The bank is offered to the next
        patient with this reason as "questions that have proved worth
        asking", ranked by how often they have been asked -- so learning
        from a question someone refused to answer teaches the corpus to
        keep asking it, and the count makes it more prominent every time
        it fails.
        """
        answered_ids = {
            entry.question_id
            for entry in self.answered
            if entry.status in (AnswerStatus.CONFIRMED, AnswerStatus.UNCERTAIN)
        }
        return [
            question.text
            for question in self.asked
            if question.category is category and question.id in answered_ids
        ]

    def as_storage(self) -> list[SymptomAnswer]:
        """The answers, shaped for the session document."""
        return [entry.model_copy() for entry in self.answered]

    def snapshot(self) -> dict[str, object]:
        """The whole screen state, as the browser renders it.

        A full snapshot rather than a delta on purpose: it is a handful of
        short pairs, it is what `connected` has to carry for a resume
        anyway, and a dropped delta would leave the patient looking at a
        question that was already answered.

        Carries no total. There is no total: the screening runs as long as
        the patient's answers warrant, so the only honest thing to show
        them is which question they are on.
        """
        current = (
            None
            if self.current is None
            else {"question_id": self.current.id, "text": self.current.text}
        )
        return {
            "current": current,
            "answers": [
                {
                    "question_id": entry.question_id,
                    "question": entry.question,
                    "answer": entry.answer,
                    # So the screen can show a declined or unremembered
                    # answer as what it is, rather than as a firm one. An
                    # added key, which the frontend's schema ignores until
                    # it is taught to read it -- no protocol break.
                    "status": entry.status.value,
                }
                for entry in self.answered
            ],
            "answered_count": len(self.answered),
        }
