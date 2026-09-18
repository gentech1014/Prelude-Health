"""Tools: the agent screens for the reason the patient actually booked, writing each question itself.

The symptom-story screen is the only screen with no fixed fields. What
belongs on it depends on why the patient booked and on what they have
already said, so it is driven as a conversation rather than a form: the
agent confirms which appointment reason it is screening, writes one
question for this patient, puts it on the screen, waits, records the
answer, and writes the next one from that answer.

Three tools, deliberately narrow:

- `start_prescreening` fixes which appointment reason is being screened and
  hands back that reason's coverage brief plus whatever has proved worth
  asking for it before.
- `ask_symptom_question` puts one question the agent has just written onto
  the patient's screen.
- `record_symptom_answer` stores what they said and clears the screen.

What this replaces, and why: the agent used to call `get_symptoms_questions`
with one of five clinical categories and then ask questions out of that
fixed set by id. Two things went wrong with that. The category had to be one
of five, so a routine checkup or an unplaceable complaint -- 23% of real
bookings, and the only two reasons with no clinical set behind them -- was
forced into whichever set sounded closest, which in practice was the
stomach set, and every such patient got the same abdominal-pain interview
regardless of what they had booked. And within a set, every patient heard
the same questions in roughly the same order, because the agent could only
re-word the eighteen it was given; a follow-up the conversation obviously
called for was simply not expressible.

How many questions a screening takes is bounded by product requirement:
`MIN_SYMPTOM_QUESTIONS` and `MAX_SYMPTOM_QUESTIONS` in
`app.agents.bidi.symptom_plan`, both enforced here and in
`navigate_to_screen` rather than left as a prompt request. Within that
range it still follows what the patient says: a clear answer closes a
thread, a vague one opens another. `MAX_SYMPTOM_CATEGORIES` is a real
limit too, and stays enforced here rather than asked for in the prompt,
because a prompt is a request.
The question guards below exist for the same reason -- the model now writes
the questions, so the checks a fixed list used to give for free (nothing
stacked, nothing repeated, nothing scored out of ten) have to be made here.
"""

import re

import structlog
from strands import tool
from strands.types.tools import ToolContext

from app.agents.bidi.call_state import live_call, reject_as_answer
from app.agents.bidi.symptom_plan import (
    MAX_QUESTION_LENGTH,
    MAX_SYMPTOM_CATEGORIES,
    MAX_SYMPTOM_QUESTIONS,
    MIN_SYMPTOM_QUESTIONS,
    SymptomQuestionPlan,
)
from app.core.constants import (
    AGENT_DRIVEN_SCREENS,
    PRESCREENING_CATEGORY_BRIEFS,
    PRESENTATION_BRIEFS,
    PRESENTATION_CAUTIONS,
    SCREEN_FIELD_LABELS,
    AnswerStatus,
    IntakeScreen,
    PrescreeningCategory,
    PresentationType,
    default_presentation_for,
    prescreening_category_reference,
    resolve_answer_status,
    resolve_prescreening_category,
    resolve_presentation_type,
)
from app.core.exceptions import ConsentNotRecordedError
from app.services.question_bank_service import QuestionBankService

log = structlog.get_logger(__name__)

_CATEGORY_MENU = ", ".join(f'"{category.value}"' for category in PrescreeningCategory)

_NUMERIC_SCALE = re.compile(
    r"\b(?:"
    r"(?:on\s+a\s+)?scale\s+of\s+\d|"
    r"scale\s+from\s+\d|"
    r"\d\s*(?:to|-|through|out\s+of)\s*10|"
    r"rate\s+(?:it|the|your)"
    r")",
    re.IGNORECASE,
)
"""Rating-scale phrasings the intake is not allowed to use.

A prompt rule until now, and one the model kept breaking under its own
clinical priors. A pre-visit intake collects what the patient can tell
you in their own words; a pain score is an instrument, and producing one
here would be the assistant assessing severity, which it is not permitted
to do."""

_PATTERN_QUESTION = re.compile(
    r"(?:"
    r"come[s]?\s+and\s+go|"
    r"comes\s+back|"
    r"constant\s+or|"
    r"or\s+(?:is\s+it\s+)?constant|"
    r"intermittent|"
    r"episode|"
    r"flare[\s-]?up|"
    r"how\s+often\s+(?:does|do|is)|"
    r"each\s+time\s+it\s+happens|"
    r"when\s+it\s+happens|"
    r"what\s+(?:brings|sets)\s+it\s+(?:on|off)|"
    r"trigger"
    r")",
    re.IGNORECASE,
)
"""Questions that assume the problem has a recurring pattern.

Refused **only** for `PresentationType.ACUTE_INJURY`, where they are
nonsense: the patient has already told you what happened, when, and doing
what. This is the reported failure in one regex -- a patient who tore a
muscle playing football was asked whether the tear "comes and goes".

Narrow on purpose, and the same shape as `_NUMERIC_SCALE` above: both are
prompt rules the model kept breaking under its own priors, moved into code
because a prompt is a request. Neither decides *what* to ask -- that is the
model's job, with the presentation and the patient's own answers in front
of it. They only refuse the small set of questions that reveal the agent
was not listening.
"""


def _presentation_block(plan: SymptomQuestionPlan) -> str:
    """What to cover given how the problem presented, and what not to ask.

    Leads the per-category brief rather than following it. The category
    briefs describe a condition unfolding over time, which is the wrong
    frame for an event that happened once at a known moment -- and a
    positive brief alone never stopped the model reaching for the pattern
    questions, because `PRESCREENING_CATEGORY_BRIEFS[NOT_SURE]` genuinely
    asks for them. The prohibitions are what close that.
    """
    presentation = plan.presentation
    if presentation is None:
        return ""
    return "\n".join(
        [
            f"## How this presented: {presentation.value.replace('_', ' ')}",
            "This is the shape of the story the patient is telling you, and it "
            "decides what is worth asking. Cover, in whatever order the "
            "conversation takes you: " + PRESENTATION_BRIEFS[presentation] + ".",
            "",
            "**" + PRESENTATION_CAUTIONS[presentation] + "**",
        ]
    )


def _known_block(context: object) -> str:
    """Everything the patient has already told you, before the screening starts.

    The screening tools used to be blind to this. `_brief` was built from
    `plan.categories` and `plan.reference` and nothing else, so the model
    walked into the symptom screen with a fresh checklist and no record of
    the answer it had taken thirty seconds earlier on `patient-concerns` --
    which is one of the two ways a long call starts re-asking things.
    """
    progress = getattr(context, "progress", None)
    if progress is None:
        return ""

    lines: list[str] = []
    for screen in AGENT_DRIVEN_SCREENS:
        values = progress.details.get(screen.value, {})
        for field_name, value in values.items():
            label = SCREEN_FIELD_LABELS.get(field_name, field_name)
            note = "" if value.status is AnswerStatus.CONFIRMED else f" [{value.status.value}]"
            lines.append(f"- {label}: {value.text}{note}")
    if not lines:
        return ""

    return "\n".join(
        [
            "## Already established -- do not ask any of this again",
            "The patient has told you these, here or on an earlier topic. Build on "
            "them. Where one already covers something on the brief below, that "
            "item is done: follow up on what they said rather than asking it cold.",
            *lines,
        ]
    )


def _brief(plan: SymptomQuestionPlan, context: object | None = None) -> str:
    """The coverage brief and reference material for every reason in play."""
    blocks = []
    if context is not None:
        known = _known_block(context)
        if known:
            blocks.append(known)

    presentation = _presentation_block(plan)
    if presentation:
        blocks.append(presentation)

    for category in plan.categories:
        label = prescreening_category_reference(category)
        lines = [
            f"## Screening: {label} (`{category.value}`)",
            "Cover, as the conversation allows and in whatever order it takes you: "
            + PRESCREENING_CATEGORY_BRIEFS[category]
            + ".",
        ]
        if plan.presentation is not None:
            lines.append(
                "This list is written for a condition that has been going on for a "
                "while. Where an item does not fit how this patient's problem "
                "actually presented, skip it -- the section above wins."
            )
        reference = plan.reference.get(category, [])
        if reference:
            lines.append(
                "Questions that have proved worth asking for this reason before. "
                "They are REFERENCE, not a script and not a list to work through: "
                "use one only where it genuinely fits what this patient has just "
                "said, and write a better one when it does not. They were written "
                "for other patients, whose problems presented differently."
            )
            lines += [f"- {question.text}" for question in reference]
        else:
            lines.append(
                "Nothing has been recorded for this reason yet, so write the whole "
                "screening yourself from the brief above and what the patient tells you."
            )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


_UNRELATED_VERDICTS = frozenset(
    {"unrelated", "off_topic", "irrelevant", "does_not_answer", "no", "none"}
)
"""Ways the model says "they did not answer what I asked".

Read from `record_symptom_answer`'s `relevance` argument. The *judgement*
is the model's -- nothing in code can tell whether "I played football"
answers "what did you eat today", and a word list that tried would be the
keyword matching this whole design is moving away from. What code does is
make the judgement binding: once the model says the reply does not answer
the question, nothing is written down and the question stays live.
"""

_PARTIAL_VERDICTS = frozenset({"partial", "partially", "incomplete", "some", "vague"})
"""Ways the model says "they answered, but not all of it".

Recorded -- half an answer is still the patient's words and losing it
would mean asking the whole thing again -- but the question stays on
screen, because there is more of it to come.
"""

_NEXT_ACTION = (
    "NEXT ACTION: call ask_symptom_question with the exact wording, WAIT for its "
    "reply, and only then say it out loud. Do not speak the question before that "
    "call -- the patient reads it on their screen, and saying it first makes you "
    "ask it twice."
)
"""The one instruction that has to be the last thing the model reads.

Every reply in this loop ends with it, because the failure it prevents is
the one a patient notices most and the one this tool exists to stop.

What went wrong without it: the recording reply said "write your next
question from what they just said", and `_progress` -- appended straight
after -- opened by saying the same thing again. Two generation cues, no
action, and the last words the model read were about following the
thread. So it wrote the question and simply said it. The screen never
changed, and when it did remember the tool a moment later, the tool
answered "ask it out loud, word for word as you passed it" -- so it said
the same question a second time. First voice-only, then on screen and
spoken again, which is exactly what was reported.

Recency is the whole point: a speech model acts on the end of what it
just read. The reasoning goes first, the action goes last.
"""


def _progress(plan: SymptomQuestionPlan) -> str:
    """Where the screening has got to, led by depth rather than by count.

    This string is re-injected into the model's context on literally every
    screening turn, which makes it the most repeated instruction in the
    system -- far more repeated than anything in the prompt, which is read
    once. The previous version opened every one of those turns with the
    quota: "at least 7 are required before you can move on. Keep going."

    That is what produced the padding. A model optimizing the instruction
    it is handed most often will reach 7 questions, and when the patient's
    real story runs out at four it reaches them by touring whatever is left
    on the brief -- which is how a torn muscle ends up being asked whether
    it comes and goes. The counterweight in the prompt ("do not pad a thin
    thread just to reach 7") was stated once, in the same sentence as the
    quota, and never repeated by anything.

    So the count is still here -- it is a real product requirement and the
    navigation gate still enforces it -- but it no longer leads, and it is
    no longer the only thing said. What leads is the instruction to follow
    the patient.
    """
    answered = len(plan.answered)
    substantive = plan.substantive_count
    lead = (
        "A question that follows their last answer is worth more than the next item on the brief."
    )

    if not answered:
        return (
            f"Nothing has been answered on this screen yet. {lead} This screening runs "
            f"to at least {MIN_SYMPTOM_QUESTIONS} questions and at most "
            f"{MAX_SYMPTOM_QUESTIONS}, but that is a range, not a target -- what "
            "decides each question is what the patient has just told you."
        )

    counted = f"{answered} answered"
    if substantive != answered:
        # Declines and "I don't know" count toward the minimum -- the
        # patient engaged -- but they are not coverage, and a model that
        # cannot see the difference will think it has learned more than
        # it has.
        counted += f" ({substantive} where they actually told you something)"

    if answered < MIN_SYMPTOM_QUESTIONS:
        return (
            f"So far: {counted}. {lead} This screening needs at least "
            f"{MIN_SYMPTOM_QUESTIONS} before the call can move on -- but reaching that "
            "by asking things this patient's problem does not raise is worse than "
            "asking nothing. Follow the thread; the count follows from it."
        )
    return (
        f"So far: {counted}, which meets the minimum. {lead} You may ask up to "
        f"{MAX_SYMPTOM_QUESTIONS}. Keep going only while their answers are still "
        "opening things up -- move on as soon as their doctor has what they need."
    )


@tool(context=True)
async def start_prescreening(category: str, tool_context: ToolContext, presentation: str = "") -> str:
    """Fix what you are screening, and get the brief for it.

    Call this once the patient has told you what the call should be about.
    It returns what a screening for that needs to cover, given both the
    body area and the *kind* of problem it is, plus questions that have
    proved worth asking before -- as reference, never as a script.

    You write every question yourself, for this patient, from that brief and
    from what they have already said. Do not read the reference questions out
    in order and do not treat them as a set you have to finish.

    Call it a second time, with a different reason, only when the patient has
    clearly raised two separate things -- for example the condition they
    booked about plus a new complaint. Work both as one conversation
    rather than one set of questions and then another.

    Args:
        category: the body area the problem belongs to -- one of
            "diabetes", "blood_pressure", "heart", "lung", "stomach",
            "general_checkup", "not_sure". Use the reason the patient booked
            under unless they have told you it is something else. If what
            they describe genuinely belongs to none of them -- an injury, a
            rash, a headache -- "not_sure" is the honest answer, and the
            `presentation` argument is what makes the screening fit.
            Never ask the patient to pick one and never say its name aloud.
        presentation: how their problem actually presented, which matters
            more than the area for what is worth asking. One of:
            "acute_injury" -- a discrete event injured them (a tear, a
            sprain, a fall, a blow, a burn); "new_problem" -- a new symptom
            with no injury behind it; "ongoing_condition" -- something they
            already know they have, being reviewed; "routine" -- no
            complaint at all; "undifferentiated" -- something is wrong and
            they cannot place it. Decide it from what they have actually
            told you, never from a single word they used. Getting this
            right is what stops a torn muscle being asked whether it comes
            and goes.
        tool_context: injected by the agent runtime; carries the
            per-call `invocation_state`.
    """
    context = live_call(tool_context.invocation_state)
    if not context.consent_given:
        # Screening questions are the clinical part of the call; handing
        # them over before consent would be collecting by another name.
        raise ConsentNotRecordedError(context.session_id)

    plan = context.symptoms
    resolved = resolve_prescreening_category(category)
    if resolved is None:
        # A usable correction, not an exception: a tool that raises here
        # surfaces as a broken turn to a patient who is mid-sentence.
        log.warning("prescreening_category_unknown", session_id=context.session_id)
        return (
            f"'{category}' is not an appointment reason. Choose the one closest to what "
            f"the patient booked or has just told you: {_CATEGORY_MENU}. If they cannot "
            'place what is wrong, "not_sure" is the right answer, not a guess at an organ.'
        )

    if resolved not in plan.categories and plan.at_category_limit:
        loaded = ", ".join(existing.value for existing in plan.categories)
        return (
            f"You are already screening for {loaded}, which is the limit of "
            f"{MAX_SYMPTOM_CATEGORIES}. Work through those with ask_symptom_question instead."
        )

    # Falls back rather than refusing: a screening with the right area and
    # a guessed presentation is recoverable, one that never starts is not.
    # The fallback reads the booking reason, which is the only presentation
    # signal available without the conversation.
    resolved_presentation = resolve_presentation_type(presentation)
    if resolved_presentation is None:
        resolved_presentation = default_presentation_for(resolved)
        if presentation:
            log.info(
                "prescreening_presentation_unknown",
                session_id=context.session_id,
                fell_back_to=resolved_presentation.value,
            )

    question_bank: QuestionBankService = tool_context.invocation_state["question_bank"]
    # The presentation is real signal the old category-only lookup never
    # had: an acute injury and an ongoing condition under the same category
    # warrant a different reference set, and a live semantic query can use
    # that where a plain `asked_count` sort could not.
    query_text = f"{prescreening_category_reference(resolved)}: {resolved_presentation.value}"
    reference = await question_bank.reference_questions_live(resolved, query_text)
    plan.start(resolved, reference, resolved_presentation)

    log.info(
        "prescreening_started",
        session_id=context.session_id,
        category=resolved.value,
        presentation=plan.presentation.value if plan.presentation else None,
        reference_questions=len(plan.reference.get(resolved, [])),
    )
    closing = (
        "Now navigate to 'symptom-story', then write your first question from what "
        "the patient has already told you and put it on their screen with "
        "ask_symptom_question."
    )
    if len(plan.categories) > 1:
        closing = (
            "You are now screening two reasons. Work them together as one "
            "conversation rather than one set then the other, and pass "
            f"category='{resolved.value}' on ask_symptom_question for the questions that "
            "belong to this one. If you are not already on 'symptom-story', navigate "
            "there first."
        )
    return (
        f"Screening {prescreening_category_reference(resolved)}.\n\n"
        + _brief(plan, context)
        + "\n\n"
        + _progress(plan)
        + "\n\n"
        + closing
        + "\n\n"
        + _NEXT_ACTION
    )


@tool(context=True)
async def ask_symptom_question(
    question_text: str, tool_context: ToolContext, category: str = ""
) -> str:
    """Put the question you have just written onto the patient's screen.

    Call this and wait for the reply *immediately before* you speak the
    question, every time, for every question on this screen. The patient
    sees one question at a time and it is this call that changes it --
    speaking without calling this leaves them reading the previous question
    while you ask the next one.

    Then stop and wait for their answer. Do not ask a second question until
    you have recorded the first with record_symptom_answer.

    Args:
        question_text: the exact wording you are about to say out loud, in
            plain conversational English. One question, about one thing,
            short enough to say in a breath. Write it for this patient from
            what they have just told you -- a follow-up on their last answer
            is usually worth more than the next item on the brief. Never
            stack two questions, never ask them to rate anything on a
            numeric scale, and never ask them to name their own condition.
        tool_context: injected by the agent runtime; carries the
            per-call `invocation_state`.
        category: leave this out. Only pass it when you are screening two
            appointment reasons and this question belongs to the second one,
            so the question is filed under the reason it actually asks about.
    """
    context = live_call(tool_context.invocation_state)
    if not context.consent_given:
        log.warning("symptom_ask_refused_before_consent", session_id=context.session_id)
        return (
            "Consent has not been recorded yet, so you cannot ask this. Stay on "
            "'confirm-details' and wait."
        )

    plan = context.symptoms
    if not plan.is_started:
        return (
            "You have not established what you are screening for yet. Confirm the "
            "reason on the patient's booking, then call start_prescreening."
        )

    if plan.at_question_max:
        log.warning(
            "symptom_question_max_hit",
            session_id=context.session_id,
            answered=len(plan.answered),
            asked=len(plan.asked),
        )
        if len(plan.answered) < MAX_SYMPTOM_QUESTIONS:
            # The other ceiling: enough questions put on screen, too few
            # answers back. Pressing on would be asking a patient who is
            # plainly not engaging to keep going, which is the opposite of
            # what a screening should do with a reluctant person.
            return (
                f"You have put {len(plan.asked)} questions on their screen and only "
                f"{len(plan.answered)} have been answered, so stop here. Do not ask "
                "another. Acknowledge in one short sentence that you will leave the "
                "rest, and move on to the next screen."
            )
        return (
            f"You have already asked the maximum of {MAX_SYMPTOM_QUESTIONS} questions on "
            "this screen. Move on to the next screen now."
        )

    rejection = _reject_question(question_text, plan)
    if rejection is not None:
        log.info("symptom_question_rejected", session_id=context.session_id, reason=rejection.code)
        return rejection.message + "\n\n" + _NEXT_ACTION

    # Falls back to the primary reason rather than refusing an unrecognized
    # one: which of two reasons a question is filed under is bookkeeping for
    # the bank, and losing a question over it would cost the patient an
    # answer their doctor needed.
    resolved = resolve_prescreening_category(category) if category else None
    asked_under = resolved if resolved in plan.categories else plan.primary_category
    if asked_under is None:
        return (
            "You have not established what you are screening for yet. Confirm the "
            "reason on the patient's booking, then call start_prescreening."
        )

    # A screening question can only be read on the screen that shows one.
    # Putting it there when the model forgot to navigate matches what
    # `record_intake_details` already does for every other screen -- without
    # it the patient is asked a question they cannot see, and nothing later
    # in the call corrects that.
    screen_caught_up = context.progress.screen is not IntakeScreen.SYMPTOM_STORY
    if screen_caught_up:
        log.warning(
            "intake_navigation_skipped",
            session_id=context.session_id,
            asked_on=context.progress.screen.value,
            screen=IntakeScreen.SYMPTOM_STORY.value,
        )
        await context.go_to(IntakeScreen.SYMPTOM_STORY)

    question = plan.ask(asked_under, question_text)
    context.publish_symptom_state()
    await context.persist_symptoms()

    log.info(
        "symptom_question_asked",
        session_id=context.session_id,
        # The id only. What a patient was asked is itself health
        # information once it is on their screen.
        question_id=question.id,
        asked=len(plan.answered) + 1,
    )
    if screen_caught_up:
        return (
            "Your question is on the patient's screen now, but you had not navigated "
            "to 'symptom-story' -- their screen has caught up. Call navigate_to_screen "
            "and wait for its reply BEFORE your first question on a new topic. Ask it "
            "out loud now, then wait for their answer."
        )
    return (
        "Your question is on the patient's screen now. Ask it out loud, word for word "
        "as you passed it, then wait for their answer."
    )


@tool(context=True)
async def record_symptom_answer(
    answer: str,
    tool_context: ToolContext,
    status: str = "confirmed",
    corrects: str | None = None,
    relevance: str = "answers",
) -> str:
    """Record the patient's answer to the question currently on their screen.

    Call this as soon as their answer lands, in their own words, then write
    your next question from what they just said. Their answer moves onto the
    answered list where they can still correct it, and the screen clears.

    If they did not know, could not remember, or would rather not say,
    record exactly that in their words with the matching `status` rather
    than leaving it out -- an honest gap is worth more to their doctor than
    a silent one, and far more than a value they were pressed into.

    Do NOT call this when the patient has not answered at all -- when they
    have asked you to move on, said "next", or talked about something else
    entirely. That is not an answer, and recording it puts a meaningless
    line in their doctor's notes. Ask the question again in different
    words, or tell them they can skip it if they would rather not say.

    Args:
        answer: what the patient said, in their own words. Never your
            interpretation of it, and never a value they did not give.
        status: how well you actually know this. "confirmed" when they
            said it plainly; "uncertain" when they hedged or guessed
            ("maybe a week", "I think so"); "undisclosed" when they would
            rather not say; "unknown" when they genuinely do not know or
            cannot remember. Defaults to "confirmed", so pass one of the
            others whenever the answer is anything less than firm -- their
            doctor reads a hedge very differently from a fact.
        relevance: whether what the patient just said actually answers the
            question on their screen. "answers" (the default) when it
            does; "partial" when they answered some of it but not the
            part you need; "unrelated" when they talked about something
            else entirely. Judge it against the question you actually
            asked -- if you asked what they ate today and they told you
            about football, that is "unrelated", and passing "answers"
            would put football in their doctor's notes as their diet.
            Nothing is recorded for "unrelated", and the question stays
            live so you can ask it again.
        corrects: leave this out for an ordinary answer. Pass it only when
            the patient is going back and correcting an EARLIER screening
            answer -- "actually it was three weeks, not two" -- and set it
            to roughly the question they are correcting, in your own
            words. Their correction then replaces that answer instead of
            the one currently on screen, and the live question stays up.
        tool_context: injected by the agent runtime; carries the
            per-call `invocation_state`.
    """
    context = live_call(tool_context.invocation_state)
    if not context.consent_given:
        log.warning("symptom_record_refused_before_consent", session_id=context.session_id)
        return "Consent has not been recorded yet, so nothing can be written down."

    plan = context.symptoms
    target = plan.current
    if corrects is not None:
        # A correction to something already answered, addressed by the
        # question it belongs to rather than by whatever happens to be on
        # screen. Without this the tool could only ever write to the live
        # question, so a patient correcting their answer to question three
        # while question seven was up had the correction written over
        # question seven -- destroying one answer and falsifying another.
        target = plan.match_asked(corrects)
        if target is None:
            return (
                f'You have not asked anything like "{corrects}" on this screen, so there '
                "is nothing to correct. If this is a new answer to the question currently "
                "on their screen, call this again without `corrects`."
            )

    if target is None:
        return (
            "No question is on the patient's screen, so there is nothing to answer. "
            "Call ask_symptom_question first."
        )

    # The relevance judgement is the model's, because it is the only thing
    # here that understands the question and the reply. Code cannot decide
    # whether "I played football" answers "what did you eat today" -- but
    # it can make sure that once the model says it does not, nothing is
    # written down and the question stays where it is.
    verdict = (relevance or "answers").strip().lower().replace("-", "_").replace(" ", "_")
    if verdict in _UNRELATED_VERDICTS:
        log.info(
            "symptom_answer_unrelated",
            session_id=context.session_id,
            question_id=target.id,
        )
        return (
            f'Nothing was recorded, and "{target.text}" is still the live question on '
            "their screen. They talked about something else, so you do not have an "
            "answer to it yet.\n\n"
            "Acknowledge what they actually said in one short clause -- ignoring it "
            "makes them repeat themselves -- then ask the same question again, shorter "
            "and more plainly. Do NOT call ask_symptom_question: the question is "
            "already on their screen and putting it there again would be asking twice. "
            "Just say it.\n\n"
            "If they steer away from it a second time, let it go: tell them that is "
            "fine, record it with status='undisclosed', and move on."
        )

    resolved_status = resolve_answer_status(status) or AnswerStatus.CONFIRMED
    refusal = reject_as_answer(answer)
    if refusal is not None and resolved_status not in (
        AnswerStatus.UNDISCLOSED,
        AnswerStatus.UNKNOWN,
    ):
        # The same boundary `record_intake_details` enforces, and for the
        # same reason. A screening answer is a line in the physician's
        # report and a signal fed back into the question bank; "move next"
        # must not become either.
        log.info("symptom_answer_rejected_non_answer", session_id=context.session_id)
        return (
            f"Nothing was recorded, because {refusal}. The question is still on their "
            "screen. Either ask it again in different words, or -- if they seem "
            "reluctant -- tell them plainly that they can skip it, and if they take "
            "that, record it with status='undisclosed'."
        )

    if refusal is not None:
        answer = (
            "Preferred not to say"
            if resolved_status is AnswerStatus.UNDISCLOSED
            else "Does not know"
        )

    is_correction = corrects is not None
    is_partial = verdict in _PARTIAL_VERDICTS
    if is_partial and resolved_status is AnswerStatus.CONFIRMED:
        # Half an answer is not a confirmed one. Saying so here means the
        # physician reads it as incomplete even if the rest never arrives.
        resolved_status = AnswerStatus.UNCERTAIN
    recorded = plan.record(
        target.id,
        answer,
        status=resolved_status,
        # The question stays on screen while there is more of it to come.
        clear_current=not (is_correction or is_partial),
    )
    if recorded is None:
        return "That answer was empty, so nothing was recorded. Ask the patient again."

    context.publish_symptom_state()
    await context.persist_symptoms()

    log.info(
        "symptom_answer_recorded",
        session_id=context.session_id,
        # Ids and counts only -- the answer itself is the patient's health
        # information and never reaches the log.
        question_id=recorded.question_id,
        answered=len(plan.answered),
    )

    if is_correction:
        live = plan.current
        still_open = (
            f' The question still on their screen is "{live.text}", and it is still '
            "waiting for an answer."
            if live is not None
            else ""
        )
        return (
            f'Corrected. "{target.text}" now reads "{recorded.answer}" -- that is the '
            "answer that counts, and the old one is gone. Acknowledge it in one short "
            f"sentence and carry on. Do not re-ask it.{still_open}"
        )

    if is_partial:
        return (
            f'Recorded what they gave you, and "{target.text}" is still the live '
            "question on their screen because the rest of it is still missing.\n\n"
            "Ask only for the missing part, in one short sentence, building on what "
            "they just said. Do NOT call ask_symptom_question -- the question is "
            "already on their screen. When the rest lands, record it again with "
            "relevance='answers' and it will replace this."
        )

    if plan.at_question_max:
        return (
            f"Recorded. That was question {MAX_SYMPTOM_QUESTIONS}, the maximum for this "
            "screen. Move on to the next screen now."
        )
    return "Recorded, and the screen is clear.\n\n" + _progress(plan) + "\n\n" + _NEXT_ACTION


class _Rejection:
    """Why a written question was not put on screen, and what to do instead."""

    __slots__ = ("code", "message")

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message


def _reject_question(text: str, plan: SymptomQuestionPlan) -> _Rejection | None:
    """Check a question the model wrote, before the patient can see it.

    Returns a correction the agent can act on rather than raising: these are
    all recoverable in the same turn, and an exception mid-sentence costs the
    patient the turn entirely.

    None of these were needed while the questions came from a fixed list.
    They are the cost of letting the model write its own, and they are worth
    paying -- the list is what made every call identical.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return _Rejection(
            "empty",
            "You passed no question, so nothing changed on screen. Write the question "
            "you are about to ask and pass it.",
        )

    if len(cleaned) > MAX_QUESTION_LENGTH:
        return _Rejection(
            "too_long",
            f"That is {len(cleaned)} characters, over the {MAX_QUESTION_LENGTH} the screen "
            "shows. It is a paragraph, not a question. Ask the single most useful thing in "
            "it and keep the rest for a follow-up.",
        )

    if cleaned.count("?") > 1:
        return _Rejection(
            "stacked",
            "That is more than one question. Ask the first one on its own, wait for the "
            "answer, then ask the next -- the screen shows one question at a time and the "
            "patient answers one at a time.",
        )

    if _NUMERIC_SCALE.search(cleaned):
        return _Rejection(
            "numeric_scale",
            "You cannot ask the patient to rate anything on a numeric scale. Ask in plain "
            "words instead -- whether it stops them doing things they would normally do, or "
            "what it keeps them from.",
        )

    if plan.presentation is PresentationType.ACUTE_INJURY and _PATTERN_QUESTION.search(cleaned):
        return _Rejection(
            "pattern_question_for_injury",
            "That question assumes this comes and goes in a pattern. It does not -- the "
            "patient told you about a single injury, and they already know what caused "
            "it. Asking that tells them you were not listening. Ask about the injury "
            "itself instead: what they felt at the moment it happened, whether they "
            "could carry on, whether they can use it now, swelling or bruising, what "
            "has changed since, or what it is stopping them doing.",
        )

    duplicate = plan.already_asked(cleaned)
    if duplicate is not None:
        answered = plan.answer_for(duplicate.id)
        if answered is not None:
            return _Rejection(
                "already_answered",
                f'The patient already answered that -- you asked "{duplicate.text}" and they '
                f'said "{answered.answer}". Do not ask it again. Ask about something on the '
                "brief they have not covered, or follow up on what they just told you.",
            )
        return _Rejection(
            "already_on_screen",
            f'You already asked "{duplicate.text}" and it is still waiting for an answer. '
            "Record their answer with record_symptom_answer before asking anything else.",
        )

    return None
