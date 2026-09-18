"""Unit tests for `QuestionBankService`, in both directions.

The bank has two jobs now. On the way in it is *reference*: what has proved
worth asking for an appointment reason before, handed to the agent as
material it may draw on while it writes its own questions. On the way out
it is *accumulation*: what a finished call actually asked, merged back in
under that reason so the next patient booking it starts from a better set.

The behaviour these pin hardest is the one that changed: an empty category
is a normal state, not a failure. The version this replaces refused to boot
the server unless every category was seeded, which was right while the bank
*was* the script -- and which would now block the two reasons that have no
hand-written set at all.
"""

from app.core.constants import PrescreeningCategory
from app.models.question_bank import BankQuestion, QuestionSource
from app.repositories.question_bank_repository import QuestionBankRepository
from app.services.question_bank_service import QuestionBankService


async def test_preload_of_an_empty_bank_does_not_fail(
    question_bank_repository: QuestionBankRepository,
) -> None:
    """A reason nobody has booked yet holds nothing, and that must not stop the server.

    The predecessor raised here. With the bank as the question source that
    was correct; with the agent writing its own questions it would refuse
    to start over a reason whose first patient has simply not called yet.
    """
    service = QuestionBankService(question_bank_repository)

    await service.preload()

    assert service.is_loaded
    assert service.reference_questions(PrescreeningCategory.NOT_SURE) == []


async def test_reference_questions_come_from_the_cache_not_a_live_query(
    seeded_question_bank_repository: QuestionBankRepository,
) -> None:
    """Mid-call latency is the reason this is a cache at all."""
    service = QuestionBankService(seeded_question_bank_repository)
    await service.preload()

    questions = service.reference_questions(PrescreeningCategory.LUNG)

    assert [question.text for question in questions] == ["sample question for lung"]


async def test_every_bookable_reason_can_be_asked_for(
    seeded_question_bank_repository: QuestionBankRepository,
) -> None:
    """Including the two that used to map to no category at all."""
    service = QuestionBankService(seeded_question_bank_repository)
    await service.preload()

    for category in PrescreeningCategory:
        assert service.reference_questions(category), category


async def test_recorded_questions_reach_the_next_call_in_this_process(
    question_bank_repository: QuestionBankRepository,
) -> None:
    """Without the in-memory refresh, the bank only improves after a restart."""
    service = QuestionBankService(question_bank_repository)
    await service.preload()

    await service.record_asked(
        PrescreeningCategory.GENERAL_CHECKUP, ["What made you book a checkup now?"]
    )

    assert [
        question.text
        for question in service.reference_questions(PrescreeningCategory.GENERAL_CHECKUP)
    ] == ["What made you book a checkup now?"]


async def test_the_same_question_asked_twice_is_one_entry_with_a_count(
    question_bank_repository: QuestionBankRepository,
) -> None:
    """Dedup is what stops a reusable corpus becoming a pile of near-duplicates."""
    service = QuestionBankService(question_bank_repository)
    await service.preload()

    await service.record_asked(PrescreeningCategory.HEART, ["Where exactly do you feel it?"])
    await service.record_asked(PrescreeningCategory.HEART, ["Where exactly do you feel it?"])

    stored = await question_bank_repository.get_by_category(PrescreeningCategory.HEART)
    assert len(stored) == 1
    assert stored[0].asked_count == 2


async def test_a_question_re_asked_within_one_call_counts_once(
    question_bank_repository: QuestionBankRepository,
) -> None:
    """The agent re-asking after a mishearing is one question, not two."""
    service = QuestionBankService(question_bank_repository)
    await service.preload()

    await service.record_asked(
        PrescreeningCategory.LUNG,
        ["Does anything set the cough off?", "Does anything set the cough off?"],
    )

    stored = await question_bank_repository.get_by_category(PrescreeningCategory.LUNG)
    assert [question.asked_count for question in stored] == [1]


async def test_reference_questions_are_ordered_by_what_has_earned_its_place(
    question_bank_repository: QuestionBankRepository,
) -> None:
    """The cut is by count, so hand-written seeds give way to what actually works."""
    service = QuestionBankService(question_bank_repository)
    await question_bank_repository.seed(
        [
            BankQuestion.build(
                PrescreeningCategory.STOMACH, "a seeded question", source=QuestionSource.SEED
            )
        ]
    )
    await service.preload()

    await service.record_asked(PrescreeningCategory.STOMACH, ["a question a real call asked"])

    assert [
        question.text for question in service.reference_questions(PrescreeningCategory.STOMACH)
    ] == ["a question a real call asked", "a seeded question"]


async def test_the_reference_set_is_capped(
    question_bank_repository: QuestionBankRepository,
) -> None:
    """Reference material, not a script to work through: the size is the difference."""
    service = QuestionBankService(question_bank_repository)
    await service.preload()
    await service.record_asked(
        PrescreeningCategory.DIABETES, [f"question number {index}?" for index in range(20)]
    )

    assert len(service.reference_questions(PrescreeningCategory.DIABETES, limit=5)) == 5


async def test_re_seeding_never_resets_what_calls_have_learned(
    question_bank_repository: QuestionBankRepository,
) -> None:
    """`make seed-db` is run against live environments; it must be safe there."""
    service = QuestionBankService(question_bank_repository)
    await service.preload()
    await service.record_asked(PrescreeningCategory.LUNG, ["Do you use an inhaler?"])

    inserted = await question_bank_repository.seed(
        [
            BankQuestion.build(
                PrescreeningCategory.LUNG, "Do you use an inhaler?", source=QuestionSource.SEED
            )
        ]
    )

    stored = await question_bank_repository.get_by_category(PrescreeningCategory.LUNG)
    assert inserted == 0
    assert [question.asked_count for question in stored] == [1]
    assert stored[0].source is QuestionSource.GENERATED


async def test_a_write_failure_never_reaches_the_caller(
    question_bank_repository: QuestionBankRepository,
) -> None:
    """This runs after the patient has hung up; raising would fail a finished call."""
    service = QuestionBankService(question_bank_repository)
    await service.preload()

    async def _explode(questions: list[BankQuestion]) -> int:
        raise RuntimeError("mongo is unreachable")

    question_bank_repository.record_asked = _explode  # type: ignore[method-assign]

    assert await service.record_asked(PrescreeningCategory.HEART, ["Does it spread anywhere?"]) == 0
