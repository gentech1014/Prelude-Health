"""Seed the prescreening question bank with a starting corpus per appointment reason.

Run once against a fresh environment: `make seed-db`. Idempotent, and
non-destructive -- entries are inserted only where nothing exists yet, so
re-running never resets a count that real calls have built up or reverts a
question someone has since reworded.

**These are no longer the questions the call asks.** The live agent writes
its own, on the spot, for the patient in front of it (see
`app.agents.tools.symptoms`). What is here is the cold-start reference set
for each reason -- material the agent may draw on when a reason has no real
history yet, offered so the first patient to book it does not meet an agent
working from a blank page. They go in at `asked_count: 0`, so a question a
real call has actually found useful outranks anything hand-written here as
soon as one exists.

They are intake prompts, not a clinical instrument. They exist to collect
what the patient already knows about their own experience, in plain
language, so the physician does not spend the first minutes of the
appointment gathering it. Nothing here scores, stratifies, or interprets
anything.

`general_checkup` and `not_sure` were not in this file before, because the
old five-category vocabulary had nowhere to put them -- which is exactly
why a routine checkup used to end up screened as a stomach complaint.
"""

import asyncio

import structlog
import typer
from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import get_settings
from app.core.constants import PrescreeningCategory
from app.models.question_bank import BankQuestion, QuestionSource
from app.repositories.question_bank_repository import QuestionBankRepository

log = structlog.get_logger(__name__)

cli = typer.Typer()


_QUESTIONS: dict[PrescreeningCategory, list[str]] = {
    PrescreeningCategory.DIABETES: [
        "How long have you been dealing with this?",
        "Have you been told before that your blood sugar runs high?",
        "Do you check your sugar at home? If so, what sort of numbers do you see?",
        "Have you noticed feeling more thirsty than usual?",
        "Are you passing urine more often than you used to, or getting up at night for it?",
        "Has your weight changed recently without you trying?",
        "Have you been feeling unusually tired?",
        "Have you noticed any changes in your eyesight, like blurring?",
        "Any numbness, tingling or burning in your feet or hands?",
        "Do you have any cuts or sores that have been slow to heal?",
        "Are you taking any medication for blood sugar at the moment?",
        "If you are, have you been able to take it as prescribed?",
        "Have you had any episodes of feeling shaky, sweaty or confused?",
        "Has anything changed recently in how you eat or how active you are?",
        "Does diabetes run in your family?",
        "Have you ever been hospitalized because of your diabetes?",
        "Do you have any other ongoing health conditions?",
        "Is there anything else about this you want your doctor to know?",
    ],
    PrescreeningCategory.BLOOD_PRESSURE: [
        "How long has this been going on?",
        "Have you been told before that your blood pressure runs high?",
        "Do you check it at home? If so, what readings have you been seeing?",
        "Have you been getting headaches?",
        "Any dizziness or light-headedness, especially standing up?",
        "Have you had any episodes of blurred vision?",
        "Any chest discomfort or shortness of breath alongside this?",
        "Are you taking any blood pressure medication at the moment?",
        "If you are, have you been able to take it regularly?",
        "Have you noticed any side effects from it?",
        "Any swelling in your ankles or feet?",
        "Has your stress level or sleep changed recently?",
        "Do you smoke, and roughly how much alcohol do you drink in a week?",
        "Does high blood pressure run in your family?",
        "Have you ever had a stroke, a heart attack, or any kidney problems?",
        "Have you ever been hospitalized because of your blood pressure?",
        "Do you have any other ongoing health conditions?",
        "Is there anything else about this you want your doctor to know?",
    ],
    PrescreeningCategory.HEART: [
        "How long have you been noticing this?",
        "Can you describe what it actually feels like, in your own words?",
        "Where exactly do you feel it?",
        "Does it come and go, or is it there all the time?",
        "How long does it last when it happens?",
        "Does anything bring it on -- activity, stress, eating, lying down?",
        "Does anything make it ease off?",
        "Does the feeling spread anywhere, like your arm, neck or jaw?",
        "Do you get short of breath with it?",
        "Have you noticed your heart racing, pounding or skipping?",
        "Any sweating, nausea or light-headedness when it happens?",
        "Any swelling in your legs or ankles?",
        "Has this stopped you doing things you would normally do?",
        "Have you had any heart problems before, or do they run in your family?",
        "Have you ever been hospitalized for a heart problem?",
        "Do you have any other ongoing health conditions?",
        "Is there anything else about this you want your doctor to know?",
    ],
    PrescreeningCategory.LUNG: [
        "How long have you had this?",
        "Can you describe what the breathing feels like?",
        "Does it happen at rest, or only when you are active?",
        "How far can you walk, or how many stairs, before you notice it?",
        "Is it worse at any particular time of day, or at night?",
        "Do you have a cough with it?",
        "If you are coughing anything up, what does it look like?",
        "Any wheezing or noisy breathing?",
        "Any chest tightness or pain when you breathe?",
        "Have you had a fever or felt generally unwell?",
        "Does anything set it off -- dust, cold air, pets, exercise?",
        "Do you use an inhaler or any breathing medication?",
        "Do you smoke now, or have you smoked in the past?",
        "Have you had chest or breathing problems before, such as asthma?",
        "Are you regularly around dust, chemicals or pollution at work or at home?",
        "Have you ever been hospitalized for a breathing problem?",
        "Do you have any other ongoing health conditions?",
        "Is there anything else about this you want your doctor to know?",
    ],
    PrescreeningCategory.STOMACH: [
        "How long has this been going on?",
        "Where in your stomach do you feel it?",
        "Can you describe what it feels like, in your own words?",
        "Does it come and go, or is it constant?",
        "Is it related to eating -- before, during or after meals?",
        "Does any particular food seem to set it off?",
        "Have you had any nausea or vomiting?",
        "Have your bowel habits changed at all?",
        "Have you noticed any blood, or very dark stools?",
        "Any heartburn or acid coming up?",
        "Has your appetite changed?",
        "Has your weight changed without you trying?",
        "Are you taking anything for it, including anything over the counter?",
        "Are you on any regular medication, such as painkillers?",
        "Have you ever been hospitalized for a stomach or digestive problem?",
        "Do you have any other ongoing health conditions?",
        "Is there anything else about this you want your doctor to know?",
    ],
    PrescreeningCategory.GENERAL_CHECKUP: [
        "What made you decide to book a checkup now?",
        "Is there anything specific you wanted to raise while you are in?",
        "When were you last seen, and what was checked then?",
        "Has anything been bothering you that you have been putting off mentioning?",
        "How has your energy been over the last few months?",
        "How have you been sleeping?",
        "Has your weight changed much, either way?",
        "How has your appetite been?",
        "How would you say your mood and stress have been lately?",
        "What does a normal week look like for you in terms of exercise?",
        "Do you smoke, and roughly how much alcohol do you drink in a week?",
        "Are you taking any regular medications or supplements?",
        "Is there any screening or vaccination you know you are due for?",
        "Is there anything in your family that you worry about for yourself?",
        "Is there anything else you want your doctor to know before you come in?",
    ],
    PrescreeningCategory.NOT_SURE: [
        "Take your time -- what feels wrong, in your own words?",
        "When did you first notice it?",
        "Did it come on suddenly, or build up gradually?",
        "Where in your body do you notice it most?",
        "Is it there all the time, or does it come and go?",
        "What were you doing when it started?",
        "Does anything seem to make it worse?",
        "Does anything make it better, even a little?",
        "Has it stopped you doing anything you would normally do?",
        "Have you noticed any change in your sleep, appetite or energy alongside it?",
        "Has your mood been affected by it?",
        "Has anything like this ever happened to you before?",
        "Have you tried anything for it so far?",
        "Are you taking any regular medications?",
        "What worries you most about it?",
    ],
}


@cli.command()
def seed() -> None:
    """Insert the starting reference corpus for every appointment reason."""
    asyncio.run(_seed())


async def _seed() -> None:
    settings = get_settings()
    client = AsyncIOMotorClient(settings.mongo_uri)
    repository = QuestionBankRepository(client[settings.mongo_db_name])

    await repository.ensure_indexes()

    total = 0
    for category, questions in _QUESTIONS.items():
        inserted = await repository.seed(
            [BankQuestion.build(category, text, source=QuestionSource.SEED) for text in questions]
        )
        total += inserted
        log.info(
            "seeded_category",
            category=category.value,
            offered=len(questions),
            # Zero on a re-run, which is the point: seeding never overwrites
            # what real calls have since recorded for this reason.
            inserted=inserted,
        )

    log.info("seed_complete", categories=len(_QUESTIONS), inserted=total)
    client.close()


if __name__ == "__main__":
    cli()
