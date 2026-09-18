"""Tool: put what the patient just said onto the screen they are looking at.

The screens are not a second data model -- the transcript remains the
source of truth. They exist so a patient watching their own answers appear
can *correct* them, which is the one thing a voice-only intake cannot
offer. The summarizer is shown these values as well, but only as a
corroborating record of what the agent believed it heard: a live
transcription drops words, and a value the agent confirmed on screen is
worth more than a gap the transcript happens to leave.

`details` is a JSON object passed as a string rather than a typed object
parameter. Two strings is a tool schema Nova Sonic fills reliably; nested
object schemas with open-ended keys are not. Parsing is deliberately
tolerant (JSON first, then `key: value` lines) because a mid-call parse
failure costs the patient a visible answer, and being strict buys nothing
-- every key is checked against the screen's allowlist afterwards anyway.

`selections` is a third flat string, and it exists because the model
already knows something the frontend then has to guess at. A patient who
says "my sugar's been bad" has named diabetes, and the model understood
that; shipping only the words meant the browser had to recover the meaning
with substring matching, which missed every synonym and -- worse -- matched
"no diabetes" as diabetes. So the model may also name the option ids it
means, from a closed list it is handed by `navigate_to_screen`.

Optional on purpose. It is best-effort extra signal, never a replacement:
the words are still recorded, unknown ids are dropped like unknown fields,
and a call where the model never fills this argument behaves exactly as it
did before -- the frontend matches the words itself. That fallback is what
makes it safe to ask a speech-to-speech model for structure mid-turn.
"""

import json
from typing import Any

import structlog
from strands import tool
from strands.types.tools import ToolContext

from app.agents.bidi.call_state import live_call
from app.core.constants import (
    SCREEN_FIELD_FOLLOW_UPS,
    SCREEN_FIELD_LABELS,
    SCREEN_FIELDS,
    SCREEN_PAIR_FIELDS,
    AnswerStatus,
    IntakeScreen,
    resolve_answer_status,
)
from app.schemas import intake_channel

log = structlog.get_logger(__name__)

_MAX_VALUE_LENGTH = 500
"""Field values are a patient's phrasing of one answer, not an essay.

Truncating protects the screen's layout; the untruncated version is
already in the transcript, which is what the physician actually reads."""


def _coerce(raw: Any) -> str:
    """Flatten one model-supplied value to the string a field can display."""
    if isinstance(raw, str):
        return raw[:_MAX_VALUE_LENGTH]
    if isinstance(raw, bool):
        return "yes" if raw else "no"
    if isinstance(raw, (int, float)):
        return str(raw)
    if isinstance(raw, list):
        return ", ".join(_coerce(item) for item in raw if item is not None)[:_MAX_VALUE_LENGTH]
    if isinstance(raw, dict):
        return ", ".join(f"{key}: {_coerce(value)}" for key, value in raw.items())[
            :_MAX_VALUE_LENGTH
        ]
    return ""


def _parse_details(details: str) -> dict[str, str]:
    """Read a JSON object, falling back to `key: value` lines."""
    text = details.strip()
    if not text:
        return {}

    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        loaded = None

    if isinstance(loaded, dict):
        return {str(key): _coerce(value) for key, value in loaded.items()}

    parsed: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip():
            parsed[key.strip().strip('"').strip("'")] = _coerce(value.strip())
    return parsed


def _parse_selections(selections: str) -> dict[str, list[str]]:
    """Read `{"field": "id-a, id-b"}` into per-field id lists.

    As tolerant as `_parse_details` and for the same reason, accepting a
    JSON object of either strings or lists, or `field: id-a, id-b` lines.
    Nothing here decides whether an id is real -- `CallProgress.record_selections`
    checks every one against the field's own option list, so a permissive
    reader cannot widen what actually reaches the screen.
    """
    text = selections.strip()
    if not text:
        return {}

    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        loaded = None

    if not isinstance(loaded, dict):
        loaded = {}
        for line in text.splitlines():
            key, separator, value = line.partition(":")
            if separator and key.strip():
                loaded[key.strip().strip('"').strip("'")] = value

    parsed: dict[str, list[str]] = {}
    for key, raw in loaded.items():
        if isinstance(raw, str):
            ids = raw.split(",")
        elif isinstance(raw, (list, tuple)):
            ids = [str(item) for item in raw]
        elif isinstance(raw, bool):
            ids = ["yes" if raw else "no"]
        else:
            continue
        cleaned = [item.strip() for item in ids if str(item).strip()]
        if cleaned:
            parsed[str(key)] = cleaned
    return parsed


def _answered(progress: Any, screen: IntakeScreen, field: str) -> str | None:
    """The value on record for one field of one screen, if there is one.

    Reads the selection first: for a yes/no the id *is* the answer, where
    the words behind it ("no known allergies") carry a polarity nothing
    downstream can reliably resolve.
    """
    selected = progress.selections.get(screen.value, {}).get(field)
    if selected:
        return selected
    recorded = progress.details.get(screen.value, {}).get(field)
    return recorded.text if recorded is not None else None


def _incomplete_pairs(field: str, value: str) -> str | None:
    """What is missing from a two-part entry, or None when both halves are there.

    The patient said they had been in hospital, the agent wrote down what
    for, and the card kept an empty "when" box that nobody went back to.
    An entry with no `:` has only its first half.
    """
    parts = SCREEN_PAIR_FIELDS.get(field)
    if parts is None:
        return None
    entries = [entry.strip() for entry in value.split(";") if entry.strip()]
    missing = [entry for entry in entries if ":" not in entry]
    if not missing:
        return None
    return f"for {', '.join(missing)} you have not got {parts.secondary}"


def _outstanding(screen: IntakeScreen, progress: Any) -> list[str]:
    """What this screen still needs asked, given everything recorded on it.

    Computed from the whole screen rather than from this call's payload:
    the gate and its follow-ups usually arrive in separate turns, so a
    reminder that only looked at what just landed would fire on neither.
    """
    gaps: list[str] = []
    for gate, follow_up in SCREEN_FIELD_FOLLOW_UPS.items():
        if gate not in SCREEN_FIELDS.get(screen, ()):
            continue
        answer = _answered(progress, screen, gate)
        if answer is None or answer.strip().casefold() != follow_up.when:
            continue
        for field, asks in follow_up.asks:
            value = _answered(progress, screen, field)
            if value is None:
                gaps.append(asks)
            else:
                incomplete = _incomplete_pairs(field, value)
                if incomplete is not None:
                    gaps.append(incomplete)
    return gaps


def _parse_certainty(certainty: str) -> dict[str, AnswerStatus]:
    """Read `{"field": "uncertain"}` into resolved statuses, dropping junk.

    As tolerant as `_parse_details` and `_parse_selections`, and for the
    same reason: this is a speech-to-speech model filling a third string
    argument mid-turn. An unreadable status falls back to `confirmed`,
    which is exactly the behaviour before this argument existed.
    """
    text = certainty.strip()
    if not text:
        return {}

    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        loaded = None

    if not isinstance(loaded, dict):
        loaded = {}
        for line in text.splitlines():
            key, separator, value = line.partition(":")
            if separator and key.strip():
                loaded[key.strip().strip('"').strip("'")] = value

    parsed: dict[str, AnswerStatus] = {}
    for key, raw in loaded.items():
        status = resolve_answer_status(raw if isinstance(raw, str) else None)
        if status is not None:
            parsed[str(key)] = status
    return parsed


def _non_answer_reply(non_answers: list[tuple[str, str]]) -> str:
    """What to tell the model when the patient has not actually answered.

    The single most important tool reply in the system, because the thing
    it refuses used to be silently accepted: a patient saying "move next"
    became the recorded answer to whatever was being asked, satisfied the
    completeness gate, advanced the call, and reached their doctor.

    Names the escape hatch explicitly. Refusing without one is how the
    model ends up writing *something* into the field -- the navigation
    gate will not let it move on, and the only exit the tool layer offers
    is a value. So the reply says what the two legitimate exits are.
    """
    described = "; ".join(
        f"{SCREEN_FIELD_LABELS.get(name, name)} -- {reason}" for name, reason in non_answers
    )
    return (
        f"Nothing was recorded for: {described}. Do not write anything into those "
        "fields until the patient has actually answered them. You have two ways "
        "forward, and both are fine: ask again in different, shorter words, or tell "
        "them plainly they can skip it if they would rather not say. If they decline, "
        'record that with certainty {"<field>": "undisclosed"}; if they genuinely '
        'do not know, use "unknown". Either of those counts as answered and lets the '
        "call move on. Never invent a value to get past this."
    )


def _next_step(screen: IntakeScreen, progress: Any, *, opened_upload: bool) -> list[str]:
    """What to tell the model to do next, in the order it should do it.

    The upload line comes first when the panel has just opened, because it
    is the thing the patient is now looking at. Everything else is the
    screen's own unanswered questions, named so the model asks them rather
    than treating a filled gate as a finished topic.
    """
    steps: list[str] = []
    if opened_upload:
        steps.append(
            "They have results, so the upload panel is now open on their screen. "
            "Say, in your own words and in one short sentence, that they can upload "
            "the report or take a photo of it right there. Do NOT ask whether they "
            "have a document -- they have just told you they do. Do not wait for the "
            "file, do not mention this tool, and never say 'carry on'."
        )

    gaps = _outstanding(screen, progress)
    if gaps:
        steps.append(
            "Still unanswered on this screen: "
            + "; ".join(gaps)
            + ". Ask for those now, one short question at a time, before you move "
            "to the next topic. If the patient does not know, accept that and record "
            "it as their answer rather than leaving it blank."
        )

    if not steps:
        steps.append("Continue.")
    return steps


@tool(context=True)
async def record_intake_details(
    screen: str,
    details: str,
    tool_context: ToolContext,
    selections: str = "",
    certainty: str = "",
) -> str:
    """Show what the patient just told you on their screen, so they can correct it.

    Call this straight after an answer lands, then carry on with the next
    question -- never wait, never read the values back out loud, and never
    ask the patient to check their screen. Use only their own words. If
    they did not give a value, leave the field out entirely rather than
    filling in something plausible.

    Never record a patient asking you to move on -- "move next", "skip
    this", "next" -- as their answer. That is them not answering, and it
    is refused. Ask again in different words, or offer them the chance to
    decline, and record the decline with `certainty`.

    Args:
        screen: the screen the value belongs on, e.g. "medication".
        details: a JSON object of field name to the patient's own words,
            e.g. {"medications": "Metformin 500 milligrams twice a day"}.
            Valid field names come from navigate_to_screen's reply.
        certainty: optional, and worth passing whenever an answer is
            anything less than firm. A JSON object of field name to one
            of: "confirmed" (they said it plainly -- the default),
            "uncertain" (they hedged or guessed), "unknown" (they do not
            know or cannot remember), "undisclosed" (they would rather
            not say), "inferred" (you worked it out from something they
            said rather than being told). For "undisclosed" and "unknown"
            you may pass any short text as the value; what is shown is
            standardized. Their doctor reads a guess very differently
            from a fact, and needs to know the difference between a
            question nobody asked and one the patient declined.
        tool_context: injected by the agent runtime; carries the
            per-call `invocation_state`.
        selections: optional, and only for fields navigate_to_screen listed
            options for. A JSON object of field name to the option ids you
            mean, comma-separated, e.g.
            {"conditions": "diabetes, high-blood-pressure"}. Use the ids
            exactly as they were given to you. This is you telling the
            screen which choices to tick; keep the patient's own words in
            `details` regardless, and never reword them to match an option.
            Only name an option the patient actually affirmed -- if they
            denied it, said they were unsure, or you did not hear an option
            that fits, leave the field out of this argument and their words
            alone will be shown.
    """
    invocation_state: dict[str, Any] = tool_context.invocation_state
    context = live_call(invocation_state)

    if not context.consent_given:
        # The hard boundary. Consent is what makes collecting anything
        # lawful, so this refuses in code rather than trusting the prompt.
        log.warning("record_refused_before_consent", session_id=context.session_id)
        return (
            "Consent has not been recorded yet, so nothing can be written down. "
            "Ask the patient to give consent on screen first."
        )

    try:
        target = IntakeScreen(screen.strip())
    except ValueError:
        return f"'{screen}' is not a screen, so nothing was recorded."

    if target is IntakeScreen.SYMPTOM_STORY:
        # Named explicitly rather than falling through to the generic
        # "no fields" reply: this screen does collect, just through a
        # different tool, and the model needs pointing at it mid-call.
        return (
            "The symptom screen is not filled with this tool. Use "
            "ask_symptom_question to put a question on it, then "
            "record_symptom_answer for the reply."
        )

    allowed = SCREEN_FIELDS.get(target, ())
    if not allowed:
        return f"'{target.value}' has no fields to fill."

    outcome = context.progress.record(
        target, _parse_details(details), statuses=_parse_certainty(certainty)
    )
    accepted = {name: value.text for name, value in outcome.accepted.items()}
    accepted_selections = context.progress.record_selections(target, _parse_selections(selections))

    # A field the patient did not actually answer must not keep the tick
    # the agent guessed for it. Refused words with a surviving selection
    # is the exact shape of "the agent's inference outlived the patient's
    # own non-answer".
    if outcome.non_answers:
        cleared = context.progress.clear(target, [name for name, _ in outcome.non_answers])
        for name in cleared:
            accepted_selections.pop(name, None)

    if not accepted and not accepted_selections:
        if outcome.non_answers:
            # The distinction that did not exist before: an unusable field
            # *name* and an unusable *answer* need opposite corrections,
            # and both used to produce "nothing recognized", which sent
            # the model hunting for a better field name when the real
            # problem was that the patient had not answered.
            log.info(
                "intake_non_answer_refused",
                session_id=context.session_id,
                screen=target.value,
                fields=sorted(name for name, _ in outcome.non_answers),
            )
            return _non_answer_reply(outcome.non_answers)
        return (
            f"Nothing recognized for '{target.value}'. Its fields are: " + ", ".join(allowed) + "."
        )

    # A value recorded for a screen the patient is not looking at means the
    # navigation was skipped, not that the value belongs somewhere else. The
    # data is the more reliable signal, so it carries the screen with it --
    # otherwise the patient watches an answer appear on a page they cannot
    # see, and is asked about a topic the screen has not caught up with.
    #
    # A warning, not an info line: catching up here means the patient was
    # asked a question while looking at the previous topic, which is the
    # exact drift `navigate_to_screen` exists to prevent. The screen is
    # fixed, the model is told, and the skip stays visible afterwards.
    screen_caught_up = target is not context.progress.screen
    if screen_caught_up:
        log.warning(
            "intake_navigation_skipped",
            session_id=context.session_id,
            asked_on=context.progress.screen.value,
            screen=target.value,
        )
        await context.go_to(target)

    context.bus.publish(intake_channel.form_prefill(target, accepted, accepted_selections))
    await context.persist_progress()

    log.info(
        "intake_details_recorded",
        session_id=context.session_id,
        screen=target.value,
        # Field names only. The values are the patient's health information
        # and must never reach the log.
        fields=sorted(accepted),
        selected_fields=sorted(accepted_selections),
    )
    recorded = f"Recorded on '{target.value}': {', '.join(sorted(accepted | accepted_selections))}."
    if screen_caught_up:
        return (
            f"{recorded} You had not navigated there, so the patient was looking at "
            "the previous topic while you asked. Their screen has caught up now. "
            "Call navigate_to_screen and wait for its reply BEFORE your first "
            "question on the next topic. Continue."
        )

    # Test results are the one answer that has a control behind it rather
    # than another question, and the model reliably got that wrong: it
    # asked a second time whether they had a document, then moved on
    # without ever opening the upload. So the panel opens itself here, and
    # the reply says the line to speak -- a patient who has just said they
    # have results should be asked for them, not asked again whether they
    # exist.
    # The last question of the call has been answered, so everything left
    # is the goodbye. This is what arms the force-end watchdog -- see
    # `LiveCallContext.closing_started`; arming it on speech alone ended
    # the call on patients who were still answering this very question.
    if target is IntakeScreen.THANK_YOU and "anything_else" in accepted:
        context.closing_started = True

    opened_upload = False
    if _answered(context.progress, target, "had_recent_tests") == "yes" and not (
        context.upload_prompted
    ):
        context.upload_prompted = True
        opened_upload = True
        context.bus.publish(intake_channel.upload_requested())
        log.info("intake_upload_opened_from_results", session_id=context.session_id)

    notes: list[str] = []
    if outcome.superseded:
        # A correction is the right outcome and the new value wins. Saying
        # so is what stops the model going on reasoning from the old one:
        # its own conversation history still holds the original answer, so
        # a silent overwrite is how the call ends up contradicting the
        # patient a few turns later.
        changes = "; ".join(
            f'{SCREEN_FIELD_LABELS.get(name, name)}: "{before}" -> "{after}"'
            for name, before, after in outcome.superseded
        )
        notes.append(
            f"This replaced what you had: {changes}. The new value is the one that "
            "counts. Acknowledge the correction in one short sentence and carry on -- "
            "do not re-ask it, and do not refer to the old value again."
        )
    if outcome.non_answers:
        notes.append(_non_answer_reply(outcome.non_answers))

    return " ".join(
        [recorded, *notes, *_next_step(target, context.progress, opened_upload=opened_upload)]
    )
