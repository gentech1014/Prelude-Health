"""Tools: the agent drives the patient's screen, and can ask where it is.

The patient does not navigate an eleven-step wizard; the conversation
does. That inverts the usual arrangement, and it needs an explicit signal
rather than inference: guessing the topic from tool calls and transcript
text means the screen lags the conversation and occasionally contradicts
it. One cheap tool call per topic change is both reliable and auditable.

The order is enforced here rather than left to the prompt. The call is a
fixed sequence of topics, so a forward jump past a screen is a topic the
patient never got asked about -- the tool refuses one and names the screen
that has to come next. Going back to a screen already covered stays
allowed, since a patient correcting an earlier answer is the one honest
reason to revisit a topic.

`navigate_to_screen` returns what the screen the patient is now looking at
actually asks about. That return value is the whole point of it being a
tool rather than a side effect -- the model reads it, so it knows what is
on screen in front of the patient and can ask about that rather than
something else. `get_current_screen` covers the case where the model has
lost track, typically after a reconnect.
"""

from typing import Any

import structlog
from strands import tool
from strands.types.tools import ToolContext

from app.agents.bidi.call_state import live_call
from app.agents.bidi.prompts import SCREEN_STEPS
from app.agents.bidi.symptom_plan import MIN_SYMPTOM_QUESTIONS
from app.core.constants import (
    AGENT_DRIVEN_SCREENS,
    PRE_CONSENT_SCREENS,
    SCREEN_FIELD_LABELS,
    SCREEN_FIELD_OPTIONS,
    SCREEN_FIELDS,
    SCREEN_ORDER,
    SCREEN_PAIR_FIELDS,
    SCREEN_TOPICS,
    AnswerStatus,
    IntakeScreen,
    missing_required_fields,
    next_screen,
)

log = structlog.get_logger(__name__)


def _already_recorded(screen: IntakeScreen, context: Any) -> str:
    """What is already on record for this screen, so it is not asked twice.

    `navigate_to_screen` used to describe a screen purely from the static
    tables -- what it asks about and which fields it has -- and never
    once mentioned what the patient had already said. Combined with a
    resume, a correction arriving out of order, or simply a long call, the
    model's only record of an earlier answer was its own conversation
    history, and re-asking became the default failure mode.

    Rendered with each value's certainty attached, so a field the patient
    declined reads as settled rather than as an empty box to go back to.
    """
    values = getattr(getattr(context, "progress", None), "details", {}).get(screen.value, {})
    if not values:
        return ""
    lines = []
    for name, value in values.items():
        label = SCREEN_FIELD_LABELS.get(name, name)
        note = "" if value.status is AnswerStatus.CONFIRMED else f" [{value.status.value}]"
        lines.append(f"{label}: {value.text}{note}")
    return (
        " Already answered here, so do not ask again -- " + "; ".join(lines) + ". "
        "A value marked undisclosed or unknown is answered: the patient declined or "
        "did not know, and pressing them again would be asking twice."
    )


def _describe(screen: IntakeScreen, context: Any = None) -> str:
    """What this screen asks, what it can fill, and which options it offers.

    The options are the point of listing them here rather than in the tool
    docstring: they differ per screen, and a model handed the closed list
    at the moment it lands on a screen can name an id in the same turn it
    hears the answer. That is what `record_intake_details`' `selections`
    argument consumes, and it is why the browser no longer has to work out
    that "my sugar's been bad" meant the Diabetes card.

    Free-text fields are listed plainly; a field with options is listed
    with them inline, so there is no second lookup for the model to get
    wrong.
    """
    fields = SCREEN_FIELDS.get(screen, ())
    described = f"The patient is now on '{screen.value}', which asks about {SCREEN_TOPICS[screen]}."
    recorded = _already_recorded(screen, context) if context is not None else ""
    if not fields:
        return described + recorded

    described += " Fields you can fill on it with record_intake_details: " + ", ".join(fields) + "."

    with_options = [
        (name, SCREEN_FIELD_OPTIONS[name]) for name in fields if name in SCREEN_FIELD_OPTIONS
    ]
    if with_options:
        listed = "; ".join(f"{name} = {' | '.join(options)}" for name, options in with_options)
        described += (
            " These are on-screen choices, so also pass their ids in `selections` "
            f"when the patient affirms one -- {listed}. Everything else on this "
            "screen is free text."
        )

    # The two-part fields render as a pair of boxes, and the encoding that
    # fills both only ever appeared in the system prompt -- one line, read
    # once, against a screen reached twenty turns later. So half the
    # entries arrived with no separator and the card kept an empty box
    # nobody went back to. Said here, it is in front of the model at the
    # moment it lands on the screen that has them.
    pairs = [(name, SCREEN_PAIR_FIELDS[name]) for name in fields if name in SCREEN_PAIR_FIELDS]
    if pairs:
        described += "".join(
            f" `{name}` holds two things per entry: write them as "
            f"`{parts.primary}: {parts.secondary}`, one entry per semicolon. An entry "
            "with only its first half leaves an empty box on their screen, so ask for "
            "the second even if the answer is vague."
            for name, parts in pairs
        )
    return described + recorded


def _screen_menu() -> str:
    return ", ".join(screen.value for screen in AGENT_DRIVEN_SCREENS)


@tool(context=True)
async def navigate_to_screen(screen: str, tool_context: ToolContext) -> str:
    """Move the patient's screen to the topic you are about to ask about.

    Call this and wait for the reply *before* you speak your first
    question on a new topic, so what the patient sees matches what you are
    asking. Asking first and navigating afterwards leaves the patient
    looking at the previous topic while you talk about the next one.

    Screens must be taken in order, one step at a time -- you cannot skip
    ahead, and trying is refused. You may go back to a screen already
    covered if the patient corrects an earlier answer. Moving to the very
    next screen is also refused if the current one still has unanswered
    required questions -- the reply names exactly which ones.

    Never announce it, never mention screens or pages out loud, and never
    ask the patient to tap anything -- from their side the screen simply
    follows the conversation.

    Args:
        screen: the next screen in the flow, one of "welcome",
            "confirm-details", "patient-concerns", "symptom-story",
            "medication", "allergies", "medical-history", "recent-care",
            "family-social-history", "thank-you". Taken in that order.
        tool_context: injected by the agent runtime; carries the
            per-call `invocation_state`.
    """
    invocation_state: dict[str, Any] = tool_context.invocation_state
    context = live_call(invocation_state)

    try:
        target = IntakeScreen(screen.strip())
    except ValueError:
        # A usable correction, not an exception: raising here surfaces as a
        # broken turn to a patient who is mid-sentence.
        log.warning("navigate_unknown_screen", session_id=context.session_id, requested=screen)
        return (
            f"'{screen}' is not a screen. The patient is still on "
            f"'{context.progress.screen.value}'. Valid screens: {_screen_menu()}."
        )

    if target not in AGENT_DRIVEN_SCREENS:
        return (
            f"'{target.value}' is not yours to navigate to. The patient is still on "
            f"'{context.progress.screen.value}'. Valid screens: {_screen_menu()}."
        )

    if not context.consent_given and target not in PRE_CONSENT_SCREENS:
        # Refused in code, not discouraged in the prompt: a screen the
        # patient has not consented to reaching should not be able to ask
        # them anything, whatever the model decides to try.
        log.warning(
            "navigate_refused_before_consent",
            session_id=context.session_id,
            requested=target.value,
        )
        return (
            "Consent has not been recorded yet, so you cannot move on. Stay on "
            "'confirm-details', ask the patient to confirm their details and give "
            "consent on screen, and wait."
        )

    current = context.progress.screen
    if target == current:
        return f"Already there. {_describe(target, context)}"

    if target is IntakeScreen.WELCOME:
        # The one backward move that is never a correction. `welcome` has
        # no fields and exists solely to hold the greeting, so going back
        # to it restarts the call -- and that is precisely what a resumed
        # call tried to do, because the opener used to tell it to begin
        # from step 1. Refused here as well as fixed there: a backward
        # move is otherwise allowed by design, and the prompt is the wrong
        # place to enforce something that must hold whatever the model
        # decides to try.
        log.warning(
            "navigate_refused_back_to_welcome",
            session_id=context.session_id,
            current=current.value,
        )
        return (
            "You cannot go back to 'welcome'. That screen only exists for the "
            "greeting, which you have already given -- greeting them again is the "
            f"clearest sign to a patient that the call restarted. {_describe(current, context)}"
        )

    if current is IntakeScreen.WELCOME:
        # Acknowledged, not performed. Moving the screen here moved it at
        # *generation* time -- Nova emits a tool call before it streams the
        # audio for the same turn -- so the introduction played over the
        # consent screen and the patient never saw `welcome` at all.
        #
        # The call is still worth making, and the prompt still asks for it:
        # a tool call closes Nova's current completion, which is the only
        # boundary that separates the greeting's audio from whatever is
        # said next. `BrowserOutput` moves them when that completion ends,
        # by which point every chunk of the greeting is ahead of the move
        # on the wire and the browser can hold it until they have heard it.
        #
        # Recording it is what makes that safe. Nova closes a completion
        # whenever it yields, so the greeting routinely spans several, and
        # `BrowserOutput` moving on the first one to arrive cut the
        # introduction off partway through -- most often with the emergency
        # notice still unsaid. This flag is the model stating that steps 1
        # and 2 are both done, and nothing else in the call can say so.
        context.greeting_finished = True
        log.info(
            "navigate_from_welcome_deferred",
            session_id=context.session_id,
            requested=target.value,
        )
        return (
            "Noted -- their screen moves to 'confirm-details' by itself as you "
            f"finish speaking, so there is nothing further to call. {_describe(target, context)} "
            "Ask them to check their details and give consent, then stop and wait."
        )

    # The flow is a fixed sequence, so a jump forward past a screen is a
    # topic nobody asked the patient about. Refused with the screen that
    # has to come first, which is a usable correction mid-sentence; a
    # backward move to something already covered stays allowed.
    current_index = SCREEN_ORDER.get(current)
    target_index = SCREEN_ORDER[target]
    if current_index is not None and target_index > current_index + 1:
        expected = next_screen(current)
        log.warning(
            "navigate_refused_out_of_order",
            session_id=context.session_id,
            current=current.value,
            requested=target.value,
        )
        return (
            f"You cannot skip to '{target.value}'. The patient is still on "
            f"'{current.value}', and the next screen is "
            f"'{expected.value if expected else current.value}'. Go there and "
            "cover it first."
        )

    # A single step forward is the one move that was never checked against
    # what the current screen actually collected -- only *which* screen
    # came next, never whether it was actually covered. That is how one
    # answer out of several questions on a screen was enough to move on:
    # nothing here compared what was asked against what SCREEN_REQUIRED_FIELDS
    # says this screen needs before it counts as covered.
    if current_index is not None and target_index == current_index + 1:
        # `symptom-story` has no fixed fields, so it is absent from
        # SCREEN_REQUIRED_FIELDS below -- it is gated on question count
        # instead, against the same product-mandated range that
        # `ask_symptom_question` enforces on the way up.
        if current is IntakeScreen.SYMPTOM_STORY:
            answered = len(context.symptoms.answered)
            if answered < MIN_SYMPTOM_QUESTIONS:
                log.warning(
                    "navigate_refused_symptom_story_incomplete",
                    session_id=context.session_id,
                    answered=answered,
                )
                return (
                    f"You cannot move on yet. This screening needs at least "
                    f"{MIN_SYMPTOM_QUESTIONS} questions answered, and only {answered} "
                    "have been so far. Keep asking about what the patient has told you."
                )

        # Answered, not merely present. The gate used to test whether the
        # key existed in `details`, which any string satisfied -- so a
        # patient saying "move next" was recorded as their answer and
        # unblocked the call. It now counts only values the patient
        # actually gave, which includes an explicit decline and an honest
        # "I don't know" but excludes the agent's own inference.
        # A gate is covered by its own dependents as well as by itself --
        # see `SCREEN_GATE_DEPENDENTS`. Without that, a patient who *had* a
        # hospital stay and described it left `no_hospital_stays` empty
        # forever, and the call could never leave the screen.
        missing = missing_required_fields(current, context.progress.answered_fields(current))
        if missing:
            log.warning(
                "navigate_refused_incomplete_screen",
                session_id=context.session_id,
                screen=current.value,
                missing=missing,
            )
            # Both the human label and the field name: the label is what
            # the agent has to turn into a spoken question, the field name
            # is what it must pass back to `record_intake_details`.
            named = ", ".join(
                f"{SCREEN_FIELD_LABELS.get(field, field)} (`{field}`)" for field in missing
            )
            return (
                f"You cannot move on yet. '{current.value}' still needs: {named}. Ask "
                "about those, one short question at a time. If the patient would "
                "rather not say, or does not know, that is a complete answer -- record "
                'it with certainty {"<field>": "undisclosed"} or "unknown" and the call '
                "moves on. Never invent a value, and never record them asking you to "
                "move on as though it were their answer."
            )

    await context.go_to(target)
    return _describe(target, context)


@tool(context=True)
def get_current_screen(tool_context: ToolContext) -> str:
    """Ask which screen the patient is currently looking at.

    Use this if you have lost track of where the call is -- for instance
    after the connection dropped and reconnected. It changes nothing.

    Args:
        tool_context: injected by the agent runtime; carries the
            per-call `invocation_state`.
    """
    context = live_call(tool_context.invocation_state)
    described = _describe(context.progress.screen, context)
    if not context.consent_given:
        return (
            f"{described} Consent has NOT been recorded yet, so you cannot collect "
            "anything or move on. Ask them to check their details and give consent "
            "on screen, then stop and wait."
        )
    step = SCREEN_STEPS.get(context.progress.screen)
    carry_on = f" Carry on from {step}." if step else ""
    return (
        f"{described} Consent is already recorded, so the greeting, the emergency "
        "notice and the consent request are all done -- never repeat any of them, "
        f"and never ask the patient to tick the consent box again.{carry_on}"
    )
