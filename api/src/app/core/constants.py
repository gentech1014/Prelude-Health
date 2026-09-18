"""Shared enums and constants used across layers.

Kept here (rather than duplicated per-module) because both the agent tools
and the persistence layer need the same category/state vocabulary.
"""

from enum import StrEnum
from typing import NamedTuple


class SymptomCategory(StrEnum):
    """The 5 symptom categories the supporting physician screens for.

    The live agent infers this silently from the patient's own words and
    must never ask the patient to name one directly.
    """

    DIABETES = "diabetes"
    BLOOD_PRESSURE = "blood_pressure"
    HEART = "heart"
    LUNG = "lung"
    STOMACH = "stomach"


class Sex(StrEnum):
    """Patient sex, per the BA's data model -- a controlled vocabulary like
    `SymptomCategory`, not free text, so it renders consistently on the
    physician PDF."""

    MALE = "male"
    FEMALE = "female"
    OTHER = "other"


class AnswerStatus(StrEnum):
    """How well the intake actually knows one thing it has written down.

    Every collected value used to be a bare `str`, which made five very
    different situations indistinguishable the moment they were stored: a
    patient who said "twice a day", one who guessed "maybe twice?", one who
    would rather not discuss it, one who genuinely cannot remember, and the
    agent's own reading of something they implied. All five became the same
    kind of fact -- a string on a screen, a line in the physician's report,
    and a satisfied completeness gate.

    Worse, so did a patient saying "move next". Because the only test of an
    answer was that the string was non-empty and had no question mark in it
    (the old `_is_answer`), a request to hurry up was recorded as the reason
    for the visit and unblocked the call.

    So certainty is now part of the value rather than something the words
    are expected to carry. The model reports it, the tools enforce what may
    be done with it, and it survives all the way to the report.
    """

    CONFIRMED = "confirmed"
    """The patient stated it plainly and it is theirs, in their own words."""

    UNCERTAIN = "uncertain"
    """They gave it, but hedged it -- "maybe a week", "I think so", "around
    March". A real answer, and one the physician must not read as firm."""

    INFERRED = "inferred"
    """The agent worked it out from context rather than being told it.

    Legitimate and often useful -- a patient who says "I've been taking the
    blue inhaler since my asthma got worse" has told you they have asthma
    without answering a question about it. It must never be presented to
    the physician as something the patient asserted, which is exactly what
    a bare string did."""

    UNDISCLOSED = "undisclosed"
    """They were asked and chose not to say. A boundary, not a gap.

    Recorded rather than left blank on purpose: "asked, declined" and
    "never asked" are different facts about a consultation, and only one of
    them is something for the physician to pick up in the room."""

    UNKNOWN = "unknown"
    """They were asked and genuinely do not know or cannot remember.

    Distinct from UNDISCLOSED: not knowing when you last had a tetanus shot
    is a different clinical datum from declining to discuss your drinking."""


ANSWERED_STATUSES: frozenset[AnswerStatus] = frozenset(
    {
        AnswerStatus.CONFIRMED,
        AnswerStatus.UNCERTAIN,
        AnswerStatus.UNDISCLOSED,
        AnswerStatus.UNKNOWN,
    }
)
"""The statuses that mean this topic was actually put to the patient.

What the completeness gates count. `INFERRED` is deliberately absent: the
agent's own reading of something is not a substitute for asking, and a gate
that accepted it would let the call advance past a question nobody asked.

`UNDISCLOSED` and `UNKNOWN` deliberately *are* present. A patient who
declines has been asked and has answered; blocking the call on them would
mean the only way forward is to badger someone who has already said no --
which is precisely the behaviour the old presence-only gate produced.
"""


def resolve_answer_status(value: str | None) -> AnswerStatus | None:
    """Best-effort read of a status the model supplied, or None if unusable.

    Tolerant in the same way `resolve_prescreening_category` is, and for the
    same reason: this arrives from a speech-to-speech model mid-turn, and
    rejecting "declined" because the enum happens to spell it "undisclosed"
    would throw away the one signal that stops a deflection being recorded
    as a fact.
    """
    if not value:
        return None
    cleaned = value.strip().lower().replace("-", "_").replace(" ", "_")
    if not cleaned:
        return None
    for candidate in AnswerStatus:
        if cleaned == candidate.value:
            return candidate
    return _STATUS_ALIASES.get(cleaned)


_STATUS_ALIASES: dict[str, AnswerStatus] = {
    "stated": AnswerStatus.CONFIRMED,
    "said": AnswerStatus.CONFIRMED,
    "certain": AnswerStatus.CONFIRMED,
    "sure": AnswerStatus.CONFIRMED,
    "clear": AnswerStatus.CONFIRMED,
    "definite": AnswerStatus.CONFIRMED,
    "vague": AnswerStatus.UNCERTAIN,
    "hedged": AnswerStatus.UNCERTAIN,
    "approximate": AnswerStatus.UNCERTAIN,
    "unsure": AnswerStatus.UNCERTAIN,
    "maybe": AnswerStatus.UNCERTAIN,
    "guess": AnswerStatus.UNCERTAIN,
    "declined": AnswerStatus.UNDISCLOSED,
    "decline": AnswerStatus.UNDISCLOSED,
    "refused": AnswerStatus.UNDISCLOSED,
    "prefers_not_to_say": AnswerStatus.UNDISCLOSED,
    "prefer_not_to_say": AnswerStatus.UNDISCLOSED,
    "rather_not_say": AnswerStatus.UNDISCLOSED,
    "withheld": AnswerStatus.UNDISCLOSED,
    "private": AnswerStatus.UNDISCLOSED,
    "dont_know": AnswerStatus.UNKNOWN,
    "doesnt_know": AnswerStatus.UNKNOWN,
    "does_not_know": AnswerStatus.UNKNOWN,
    "not_known": AnswerStatus.UNKNOWN,
    "cannot_remember": AnswerStatus.UNKNOWN,
    "forgot": AnswerStatus.UNKNOWN,
    "deduced": AnswerStatus.INFERRED,
    "implied": AnswerStatus.INFERRED,
    "assumed": AnswerStatus.INFERRED,
    "derived": AnswerStatus.INFERRED,
}
"""Words the model reaches for instead of the five canonical status values.

Kept beside `resolve_answer_status` rather than inside it so the mapping is
readable as data. Nothing here widens what a status can *do* -- every caller
still switches on the resolved `AnswerStatus`.
"""


class SessionState(StrEnum):
    """Session lifecycle states.

    Reconciles three conflicting definitions: the BA full-scope doc (10
    states), the BA happy-path doc (8 states, dropping DECLINED and
    EXPIRED), and the architecture doc (9 states under different names).
    BA naming wins, since the BA docs are the requirements source.

    INTERRUPTED is additive too: it is what a dropped socket produces, so
    an abandoned-but-resumable call is distinguishable from one still on
    the line. Without it a lost connection left a session IN_PROGRESS
    forever, with no report and no way to tell the two apart.

    SUMMARIZING and SUMMARY_FAILED are additive over both BA docs: they
    let the physician view distinguish "being generated" from "failed"
    rather than rendering an empty summary as though it were complete.

    CANCELLED is reached directly from IN_PROGRESS, whether the patient
    cancels mid-call (the voice tool) or via the REST button -- both
    paths funnel through the same guarded write, so exactly one of them
    wins a race. See `app.services.scheduling_service`.

    The happy-path demo never visits DECLINED, EXPIRED, or
    SUMMARY_FAILED -- but they are implemented, not stubbed, so a real
    session that hits one degrades honestly instead of lying.
    """

    BOOKING_CREATED = "booking_created"
    AI_LINK_READY = "ai_link_ready"
    NOTIFICATION_SENT = "notification_sent"
    STARTED = "started"
    IN_PROGRESS = "in_progress"
    INTERRUPTED = "interrupted"
    DECLINED = "declined"
    COMPLETED = "completed"
    SUMMARIZING = "summarizing"
    SUMMARY_READY = "summary_ready"
    SUMMARY_FAILED = "summary_failed"
    VIDEO_READY = "video_ready"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class SlotStatus(StrEnum):
    """Lifecycle of one bookable appointment slot.

    HELD is a short-lived, self-expiring intermediate state used only
    while a reschedule offer is in flight (see `SchedulingService`) --
    a slot with `status=HELD` and a past `held_until` is treated as
    takeable by every availability query, so an abandoned hold releases
    itself with no background job required for correctness.
    """

    FREE = "free"
    HELD = "held"
    BOOKED = "booked"


QUESTIONS_PER_CATEGORY_TARGET = 15
"""Approximate reference-corpus size per category, per the BA scope.

A target for the accumulated bank, no longer a budget for one call: the
agent writes its own questions and the bank is what it may draw on, so
a category can sit under this (a reason nobody has booked yet) or well
over it without either being wrong."""


class IntakeScreen(StrEnum):
    """The screens of the patient app, as the live agent addresses them.

    This is a *shared contract*, not an internal detail: every member's
    value is verbatim the patient app's own route segment under
    `/prescreen/{session_id}/`, and the live agent moves the patient
    between them by calling `navigate_to_screen`. Renaming a member here
    without renaming the frontend route silently desynchronizes the call
    from the screen the patient is looking at.

    WELCOME is where the agent greets the patient, and CONFIRM_DETAILS is
    where consent is taken. The agent is live for both -- it has to be, or
    the patient would sit in silence waiting for a greeting -- but until
    consent is recorded it can do nothing except greet, ask, and wait:
    every collection tool refuses. See `app.agents.bidi.call_state`.
    """

    WELCOME = "welcome"
    CONFIRM_DETAILS = "confirm-details"
    APPOINTMENT_SCHEDULE = "appointment-schedule"
    APPOINTMENT_RESCHEDULE = "appointment-reschedule"
    PATIENT_CONCERNS = "patient-concerns"
    SYMPTOM_STORY = "symptom-story"
    MEDICATION = "medication"
    ALLERGIES = "allergies"
    MEDICAL_HISTORY = "medical-history"
    RECENT_CARE = "recent-care"
    FAMILY_SOCIAL_HISTORY = "family-social-history"
    THANK_YOU = "thank-you"


AGENT_DRIVEN_SCREENS: tuple[IntakeScreen, ...] = (
    IntakeScreen.WELCOME,
    IntakeScreen.CONFIRM_DETAILS,
    IntakeScreen.PATIENT_CONCERNS,
    IntakeScreen.SYMPTOM_STORY,
    IntakeScreen.MEDICATION,
    IntakeScreen.ALLERGIES,
    IntakeScreen.MEDICAL_HISTORY,
    IntakeScreen.RECENT_CARE,
    IntakeScreen.FAMILY_SOCIAL_HISTORY,
    IntakeScreen.THANK_YOU,
)
"""The screens the agent may navigate to, in the order the call works through them.

APPOINTMENT_SCHEDULE is absent on purpose: it is a read-back with nothing
to answer, and reading the appointment aloud before the patient has said
why they are calling delays the only part of the conversation that matters.
It stays a real screen -- reachable from the closing recap, and where a
reschedule starts -- just not a step the intake walks through.
"""


SCREEN_ORDER: dict[IntakeScreen, int] = {
    screen: index for index, screen in enumerate(AGENT_DRIVEN_SCREENS)
}
"""Each agent-driven screen's position in the flow, for enforcing the order.

The call is a fixed sequence, so a jump forward past a screen is a skipped
topic, not a shortcut -- `navigate_to_screen` refuses one. Moving back to
a screen already covered stays allowed: a patient correcting something
they said earlier is the one reason to revisit a topic.
"""


def next_screen(current: IntakeScreen) -> IntakeScreen | None:
    """The one screen the call may move forward to from `current`.

    `None` at the end of the flow, and for a screen outside it -- neither
    has a next step the agent is allowed to take.
    """
    index = SCREEN_ORDER.get(current)
    if index is None or index + 1 >= len(AGENT_DRIVEN_SCREENS):
        return None
    return AGENT_DRIVEN_SCREENS[index + 1]


PRE_CONSENT_SCREENS: frozenset[IntakeScreen] = frozenset(
    {IntakeScreen.WELCOME, IntakeScreen.CONFIRM_DETAILS}
)
"""The only screens reachable before consent is recorded.

The agent greets on one and asks for consent on the other. Navigating
past them is refused outright rather than left to the prompt: a screen the
patient has not consented to reaching is a screen that should not be able
to ask them anything.
"""


CLOSING_SCREENS: frozenset[IntakeScreen] = frozenset(
    {IntakeScreen.THANK_YOU, IntakeScreen.APPOINTMENT_RESCHEDULE}
)
"""The screens a call sits on once the intake itself is finished.

`thank-you` carries the last question and the goodbye.
`appointment-reschedule` is the one branch off it, reached only when the
patient answers that question by asking to move their appointment. The
force-end watchdog watches both (see `app.api.ws.channels`): a patient who
walks away while choosing a new time is as gone as one who walks away
mid-goodbye, and the screening behind them is just as finished.
"""


SCREEN_TOPICS: dict[IntakeScreen, str] = {
    IntakeScreen.WELCOME: "your own introduction -- nothing is asked of the patient here",
    IntakeScreen.CONFIRM_DETAILS: "their name, date of birth and contact number",
    IntakeScreen.APPOINTMENT_SCHEDULE: "the appointment date, time and doctor",
    IntakeScreen.APPOINTMENT_RESCHEDULE: (
        "the open times they can move their appointment to -- the same list you were "
        "handed, shown as tappable day and time cards"
    ),
    IntakeScreen.PATIENT_CONCERNS: "what is bringing them in, in their own words",
    IntakeScreen.SYMPTOM_STORY: (
        "the detail of what they are experiencing -- one screening question at a "
        "time, written by you for the appointment reason you are screening"
    ),
    IntakeScreen.MEDICATION: "medicines, vitamins and supplements they take",
    IntakeScreen.ALLERGIES: "allergies and what happens when they are exposed",
    IntakeScreen.MEDICAL_HISTORY: "ongoing conditions and hospital stays",
    IntakeScreen.RECENT_CARE: "other clinicians seen recently, and recent tests",
    IntakeScreen.FAMILY_SOCIAL_HISTORY: "family conditions, tobacco, alcohol, work and home",
    IntakeScreen.THANK_YOU: "whether there is anything they would like help with",
}
"""One plain-language line per screen, describing what it asks about.

Fed to the live agent's prompt so the model's mental model of each screen
comes from the same place the frontend's routing does, rather than from a
description written separately and drifting from it.
"""


SCREEN_FIELDS: dict[IntakeScreen, tuple[str, ...]] = {
    IntakeScreen.WELCOME: (),
    # Phone only. The name and date of birth on this screen come from the
    # booking record and are what the patient is being asked to confirm --
    # letting the agent write them means it can quietly replace the values
    # under that question. It did: a "yes that is all correct" produced a
    # recorded date of birth of 1990-01-01 for a patient booked as
    # 1990-03-04. A patient whose details are wrong edits the field, which
    # reaches the transcript as their own turn.
    IntakeScreen.CONFIRM_DETAILS: ("phone_number",),
    IntakeScreen.APPOINTMENT_SCHEDULE: (),
    # The reschedule screen collects a slot, not a field: the patient taps a
    # time or names one aloud, and `move_appointment` is what writes it.
    IntakeScreen.APPOINTMENT_RESCHEDULE: (),
    IntakeScreen.PATIENT_CONCERNS: ("concerns", "concern_details"),
    # Empty on purpose. This screen's content is a sequence of questions the
    # agent picks from the question bank mid-call, not a fixed set of fields,
    # so it is driven by `app.agents.tools.symptoms` instead.
    IntakeScreen.SYMPTOM_STORY: (),
    IntakeScreen.MEDICATION: ("medications",),
    IntakeScreen.ALLERGIES: ("no_known_allergies", "allergies"),
    IntakeScreen.MEDICAL_HISTORY: (
        "conditions",
        "condition_details",
        "no_hospital_stays",
        "hospital_stays",
    ),
    IntakeScreen.RECENT_CARE: (
        "saw_other_provider",
        "provider_who",
        "provider_when",
        "provider_reason",
        "had_recent_tests",
        "test_details",
    ),
    IntakeScreen.FAMILY_SOCIAL_HISTORY: (
        "family_conditions",
        "family_details",
        "tobacco_use",
        "alcohol_use",
        "occupation",
        "living_situation",
    ),
    IntakeScreen.THANK_YOU: ("anything_else",),
}
"""Which fields each screen can display, keyed exactly as the frontend keys them.

An allowlist, not a hint: `record_intake_details` drops anything the model
names that is not in here, so a hallucinated field can never reach the
patient's screen. Field *values* are always the patient's own words, never
normalized or inferred -- the frontend matches them against its own option
labels and shows only what actually matches.
"""


SCREEN_REQUIRED_FIELDS: dict[IntakeScreen, frozenset[str]] = {
    IntakeScreen.PATIENT_CONCERNS: frozenset({"concerns"}),
    IntakeScreen.MEDICATION: frozenset({"medications"}),
    IntakeScreen.ALLERGIES: frozenset({"no_known_allergies"}),
    IntakeScreen.MEDICAL_HISTORY: frozenset({"conditions", "no_hospital_stays"}),
    IntakeScreen.RECENT_CARE: frozenset({"saw_other_provider", "had_recent_tests"}),
    IntakeScreen.FAMILY_SOCIAL_HISTORY: frozenset(
        {"family_conditions", "tobacco_use", "alcohol_use", "occupation", "living_situation"}
    ),
}
"""The subset of each screen's `SCREEN_FIELDS` that must actually be
recorded before `navigate_to_screen` will move the call forward off it.

Deliberately a subset, not every field in `SCREEN_FIELDS`: an elaboration
field (`concern_details`, `condition_details`, `family_details`) is
optional by design, and a conditional one (`allergies`, `hospital_stays`,
`provider_who`/`provider_when`/`provider_reason`, `test_details`) only
applies when its own yes/no gate field says so -- requiring it
unconditionally would block a patient who correctly answered "no" on that
gate. Requiring the gate field itself is enough: a "no" answer *is* the
complete answer to that half of the screen.

A screen absent from this table (`welcome`, `confirm-details`,
`appointment-schedule`, `symptom-story`, `thank-you`) has no completeness
gate at all -- `confirm-details` only ever holds a correction, which is
legitimately never given; `symptom-story` has no fixed fields to begin
with (see `SCREEN_FIELDS`); and `thank-you` is the last screen, with
nothing further to move forward into.
"""


SCREEN_GATE_DEPENDENTS: dict[str, frozenset[str]] = {
    "no_known_allergies": frozenset({"allergies"}),
    "no_hospital_stays": frozenset({"hospital_stays"}),
    "saw_other_provider": frozenset({"provider_who", "provider_when", "provider_reason"}),
    "had_recent_tests": frozenset({"test_details"}),
}
"""For each yes/no gate in `SCREEN_REQUIRED_FIELDS`, what a *yes* fills in.

`SCREEN_REQUIRED_FIELDS` requires only the gate, on the reasoning that a
"no" is a complete answer to that half of a screen without its dependent
field. True, and it misses the mirror case: a patient who names an allergy
and what it does to them, or describes a hospital stay and roughly when,
has just as plainly answered whether they have one -- and the model, which
now has somewhere real to put that answer, routinely never records the gate
at all.

The call then could not leave the screen. `navigate_to_screen` refused
every attempt for a field nobody had any reason to ask about, and the
model, having been refused, went on asking the next topic's questions
anyway -- so the patient sat reading allergies while being asked about
hospital stays, for the rest of the screen. Intermittent by nature: it
happened when the answer was yes and not when it was no.
"""


def missing_required_fields(screen: IntakeScreen, answered: frozenset[str] | set[str]) -> list[str]:
    """Which of this screen's required fields still have nothing behind them.

    A gate counts as covered by its own dependents, per
    `SCREEN_GATE_DEPENDENTS` -- otherwise answering it with a yes and then
    detailing that yes leaves the screen permanently incomplete.
    """
    required = SCREEN_REQUIRED_FIELDS.get(screen, frozenset())
    return sorted(
        field
        for field in required
        if field not in answered
        and not (SCREEN_GATE_DEPENDENTS.get(field, frozenset()) & set(answered))
    )


SCREEN_FIELD_LABELS: dict[str, str] = {
    "full_name": "Full name",
    "date_of_birth": "Date of birth",
    "phone_number": "Phone number",
    "concerns": "Reason for the visit",
    "concern_details": "More about the concern",
    "medications": "Medications",
    "no_known_allergies": "No known allergies",
    "allergies": "Allergies",
    "conditions": "Ongoing conditions",
    "condition_details": "More about the conditions",
    "no_hospital_stays": "No hospital stays",
    "hospital_stays": "Hospital stays",
    "saw_other_provider": "Seen anyone else recently",
    "provider_who": "Who they saw",
    "provider_when": "When they saw them",
    "provider_reason": "Why they saw them",
    "had_recent_tests": "Recent tests",
    "test_details": "Test detail",
    "occupation": "Occupation",
    "living_situation": "Living situation",
    "family_conditions": "Family conditions",
    "family_details": "More about family history",
    "tobacco_use": "Tobacco use",
    "alcohol_use": "Alcohol use",
    "anything_else": "Anything they would like help with",
}
"""Human labels for the fields above.

Used when a *typed* answer is folded back into the transcript, so the
summarizer reads "Date of birth: 4 March 1978" rather than a snake_case
key it has to decode.
"""


SCREEN_FIELD_OPTIONS: dict[str, tuple[str, ...]] = {
    # Multi-select card grids. Ids are the frontend's own option ids.
    "concerns": (
        "pain-or-discomfort",
        "injury-follow-up",
        "chronic-condition-management",
        "routine-check-up",
        "mental-health-support",
        "preventive-care-or-wellness",
        "other",
    ),
    "conditions": (
        "diabetes",
        "high-blood-pressure",
        "asthma-or-lung-condition",
        "heart-disease",
        "thyroid-disorder",
        "anxiety-or-depression",
        "other",
    ),
    "family_conditions": (
        "diabetes",
        "heart-disease",
        "high-blood-pressure",
        "cancer",
        "stroke",
        "mental-health-conditions",
        "other",
    ),
    # Single-choice pill rows. The frontend uses the visible label as the
    # value, so these ids are the labels verbatim -- matching it exactly is
    # what keeps this a validation table rather than a second mapping.
    "tobacco_use": ("Never", "Former", "Current"),
    "alcohol_use": ("Never", "Occasional", "Regular"),
    # Yes/no controls.
    "no_known_allergies": ("yes", "no"),
    "no_hospital_stays": ("yes", "no"),
    "saw_other_provider": ("yes", "no"),
    "had_recent_tests": ("yes", "no"),
}
"""The closed vocabulary each non-free-text field accepts, keyed by field.

The model is shown these ids by `navigate_to_screen` and may return them
through `record_intake_details`'s `selections` argument, alongside -- never
instead of -- the patient's own words. An id outside this table is dropped
exactly the way an unknown *field* is, so a hallucinated option can no more
reach the patient's screen than a hallucinated field can.

Kept here rather than derived from the frontend because this is the layer
that has to refuse bad input. `contracts/intake-field-options.json` is
generated from this table and asserted against the frontend's own option
lists on both sides, so the two cannot drift silently.

A field absent from this table is free text: there is nothing to select,
and only the words are carried.
"""


BOOLEAN_SCREEN_FIELDS: frozenset[str] = frozenset(
    field for field, options in SCREEN_FIELD_OPTIONS.items() if options == ("yes", "no")
)
"""Fields whose control is a yes/no, so their selection is a polarity.

Named separately because these are the fields where a wrong answer is not
a missing tick but an inverted clinical claim -- `no_known_allergies` set
from a misread "no" tells the physician the patient *has* allergies.
"""


class FieldFollowUp(NamedTuple):
    """What a yes/no answer opens up, and when."""

    when: str
    """The gate's value that means there IS something more to ask.

    Not always "yes": `no_hospital_stays` is phrased as a denial, so it is
    a *"no"* that opens the follow-up. Spelling the polarity out here is
    what stops the reminder firing on exactly the patients it should not."""

    asks: tuple[tuple[str, str], ...]
    """`(field, what to ask for)` pairs, in the order to ask them."""


SCREEN_FIELD_FOLLOW_UPS: dict[str, FieldFollowUp] = {
    "saw_other_provider": FieldFollowUp(
        when="yes",
        asks=(
            ("provider_who", "who they saw"),
            ("provider_when", "when they saw them"),
            ("provider_reason", "what it was about"),
        ),
    ),
    "had_recent_tests": FieldFollowUp(
        when="yes",
        asks=(("test_details", "which tests they had"),),
    ),
    "no_hospital_stays": FieldFollowUp(
        when="no",
        asks=(("hospital_stays", "what the stay was for, and roughly when"),),
    ),
    "no_known_allergies": FieldFollowUp(
        when="no",
        asks=(("allergies", "what they react to, and what happens"),),
    ),
}
"""The questions a yes/no answer is supposed to lead to.

Every one of these screens shows a yes/no with more fields *behind* it,
and the agent kept answering the gate and moving straight on -- a patient
who said they had been in hospital had "appendix" recorded and was never
asked when, leaving a card half-filled that nobody would go back to.

`record_intake_details` reads this after every write and names whatever is
still unanswered in its reply, so the omission is corrected mid-call
rather than surviving into the report. A prompt step alone did not hold:
it is one line the model reads once, against a tool reply it reads every
single time it records something.
"""


class FieldPairParts(NamedTuple):
    """What the two halves of a repeating two-part field mean."""

    primary: str
    secondary: str


SCREEN_PAIR_FIELDS: dict[str, FieldPairParts] = {
    "hospital_stays": FieldPairParts("what the stay was for", "roughly when it was"),
    "allergies": FieldPairParts("what they react to", "what happens when they are exposed"),
}
"""Fields the screen renders as a repeating pair of inputs.

Recorded as `primary: secondary`, one entry per `;` -- the format
`app.agents.tools.intake_details` documents and the frontend's
`parseRecordedPairs` reads back. Half an entry renders as a card with an
empty box on it, so the tool checks for a missing second half and asks for
it by name.
"""


class CareModality(StrEnum):
    """How a doctor sees patients. Shown on the booking UI's provider card."""

    IN_PERSON = "in_person"
    VIRTUAL = "virtual"
    IN_PERSON_AND_VIRTUAL = "in_person_and_virtual"


class BookingVisitType(StrEnum):
    """What a patient can pick when booking.

    A deliberate superset of `SymptomCategory`, and a *booking* vocabulary
    rather than a clinical one. The five condition types map 1:1 onto a
    symptom category; the last two map to nothing.

    Those two exist because the condition list cannot answer "I want a
    routine checkup" or "something is wrong and I do not know what" -- and a
    patient who cannot find themselves in the list will pick the closest
    wrong option, which is worse than no pre-scoping at all.

    They are NOT new `SymptomCategory` members on purpose. That enum is the
    clinical vocabulary: the question bank is keyed by it and
    `QuestionBankService.preload` refuses to start the server if any member
    lacks a seeded question set, and the live agent silently routes to one of
    exactly those five. A booking-time convenience must not change either.
    An unmapped visit type simply books with no category, and the agent
    infers one from the patient's own words during the call -- which is what
    it already does regardless of what was picked here.
    """

    DIABETES = "diabetes"
    BLOOD_PRESSURE = "blood_pressure"
    HEART = "heart"
    LUNG = "lung"
    STOMACH = "stomach"
    GENERAL_CHECKUP = "general_checkup"
    NOT_SURE = "not_sure"


class VisitTypeCopy(NamedTuple):
    """Presentation and routing for one bookable visit type."""

    label: str
    description: str
    symptom_category: SymptomCategory | None
    spoken_topic: str | None = None
    """How to refer to this reason inside a spoken sentence, or None.

    `label` is first-person copy on a card the patient taps, and it is the
    wrong thing to say back to them. Reusing it produced the single most
    jarring line the call has ever spoken: a patient who tapped "I am not
    sure" was greeted with "You've booked this appointment about I am not
    sure. Shall we go through some questions about that?"

    `None` is not a missing translation -- it means this option is a
    declaration that the patient could *not* name a reason, so there is no
    topic to confirm and the call must open with an open question instead.
    See `_booking_block` in `app.agents.bidi.prompts`.
    """


BOOKING_VISIT_TYPES: dict[BookingVisitType, VisitTypeCopy] = {
    BookingVisitType.GENERAL_CHECKUP: VisitTypeCopy(
        "General checkup",
        "A routine visit with no specific problem",
        None,
        spoken_topic="a general checkup",
    ),
    BookingVisitType.NOT_SURE: VisitTypeCopy(
        "I am not sure",
        "Something feels wrong and you cannot place it",
        None,
        # Deliberately None: see `spoken_topic`. There is nothing to
        # confirm, because the patient told the booking form they could
        # not say.
        spoken_topic=None,
    ),
    BookingVisitType.DIABETES: VisitTypeCopy(
        "Diabetes and blood sugar",
        "Blood sugar, energy, thirst, and related changes",
        SymptomCategory.DIABETES,
        spoken_topic="your blood sugar",
    ),
    BookingVisitType.BLOOD_PRESSURE: VisitTypeCopy(
        "Blood pressure",
        "Headaches, dizziness, and blood pressure readings",
        SymptomCategory.BLOOD_PRESSURE,
        spoken_topic="your blood pressure",
    ),
    BookingVisitType.HEART: VisitTypeCopy(
        "Heart",
        "Chest discomfort, palpitations, and breathlessness",
        SymptomCategory.HEART,
        spoken_topic="your heart",
    ),
    BookingVisitType.LUNG: VisitTypeCopy(
        "Lung and breathing",
        "Cough, wheezing, and shortness of breath",
        SymptomCategory.LUNG,
        spoken_topic="your breathing",
    ),
    BookingVisitType.STOMACH: VisitTypeCopy(
        "Stomach and digestion",
        "Pain, nausea, appetite, and bowel changes",
        SymptomCategory.STOMACH,
        spoken_topic="your stomach",
    ),
}
"""Every bookable visit type, in the order the booking screen shows them.

The two unscoped options lead deliberately: a patient who does not already
have a label for their problem should meet an option that fits before a wall
of conditions they have to rule themselves out of.
"""


SYMPTOM_CATEGORY_LABELS: dict[SymptomCategory, tuple[str, str]] = {
    SymptomCategory.DIABETES: (
        "Diabetes and blood sugar",
        "Blood sugar, energy, thirst, and related changes",
    ),
    SymptomCategory.BLOOD_PRESSURE: (
        "Blood pressure",
        "Headaches, dizziness, and blood pressure readings",
    ),
    SymptomCategory.HEART: (
        "Heart",
        "Chest discomfort, palpitations, and breathlessness",
    ),
    SymptomCategory.LUNG: (
        "Lung and breathing",
        "Cough, wheezing, and shortness of breath",
    ),
    SymptomCategory.STOMACH: (
        "Stomach and digestion",
        "Pain, nausea, appetite, and bowel changes",
    ),
}
"""Plain-language label and one-line description per category, for the booking UI.

Patient-facing copy lives beside the vocabulary it describes so the booking
API can serve it as live data instead of the frontend re-deriving names from
enum values. Keys are exhaustive over `SymptomCategory` by design -- a new
category must be given copy here rather than silently rendering as its raw
enum value.
"""


class AvailabilitySource(StrEnum):
    """Where a day's bookable slots were derived from.

    Carried through to the booking UI so an unverified clinic-hours slot is
    never presented as though the doctor's own calendar had confirmed it.
    """

    CALENDAR = "calendar"
    """Checked against the doctor's own Google Calendar free/busy."""

    CLINIC_HOURS = "clinic_hours"
    """Clinic opening hours minus appointments booked through this service --
    the doctor has not connected Google, or their calendar was unreachable."""


PrescreeningCategory = BookingVisitType
"""The vocabulary the live call's screening questions are organized by.

Deliberately `BookingVisitType` itself rather than a parallel enum: the
appointment reason a patient actually books under IS the category their
pre-screening belongs to, and inventing a second list would immediately
raise the question of how the two map -- which is exactly the gap that
sent every unmapped booking into a generic symptom interview.

It is NOT `SymptomCategory`. That enum is the *clinical specialty*
vocabulary the `doctors` collection is matched on, and it has no member
for a routine checkup or for a patient who cannot place what is wrong.
Those two were 23% of the bookings in this deployment, and under the old
wiring they mapped to no category at all -- so the agent had nothing to
load and fell back to whichever seeded set sounded most like unexplained
pain, which is why every such call ended up asking about stomach upset.

The five condition values are string-identical to their `SymptomCategory`
counterparts, so question-bank documents and session categories written
under the old vocabulary still resolve unchanged.
"""


PRESCREENING_CATEGORY_BRIEFS: dict[PrescreeningCategory, str] = {
    PrescreeningCategory.DIABETES: (
        "how long blood sugar has been a problem and whether it has ever been "
        "diagnosed; home glucose readings if they take them; thirst, urination "
        "and night waking; unintended weight change; fatigue; blurring of "
        "vision; numbness, tingling or burning in the feet or hands; cuts or "
        "sores that heal slowly; current glucose medication and whether they "
        "manage to take it; episodes of feeling shaky, sweaty or confused; "
        "recent changes in diet or activity"
    ),
    PrescreeningCategory.BLOOD_PRESSURE: (
        "how long it has been raised and whether it has been diagnosed; home "
        "readings if they take them; headaches; dizziness or light-headedness, "
        "especially on standing; blurred vision; chest discomfort or "
        "breathlessness alongside it; current blood-pressure medication, "
        "whether they take it regularly and any side effects; ankle or foot "
        "swelling; recent changes in stress, sleep, salt, alcohol or smoking; "
        "any previous stroke, heart attack or kidney problem"
    ),
    PrescreeningCategory.HEART: (
        "what the sensation actually feels like in their own words and exactly "
        "where they feel it; whether it comes and goes or is constant, and how "
        "long an episode lasts; what brings it on -- exertion, stress, eating, "
        "lying down -- and what eases it; whether it spreads to the arm, neck "
        "or jaw; breathlessness with it; racing, pounding or skipping beats; "
        "sweating, nausea or light-headedness during an episode; leg or ankle "
        "swelling; what it has stopped them doing; previous cardiac problems"
    ),
    PrescreeningCategory.LUNG: (
        "how long the breathing problem has been there and what it feels like; "
        "whether it happens at rest or only on exertion, and how far they can "
        "walk or how many stairs before noticing; whether it is worse at night "
        "or at a particular time of day; cough, and what if anything is coughed "
        "up; wheeze or noisy breathing; chest tightness or pain on breathing; "
        "fever or feeling generally unwell; triggers such as dust, cold air, "
        "pets or exercise; inhalers or breathing medication; smoking now or in "
        "the past; occupational or household dust and fumes"
    ),
    PrescreeningCategory.STOMACH: (
        "how long it has been going on and where in the abdomen they feel it; "
        "what it feels like in their own words; whether it is constant or comes "
        "and goes; its relationship to meals; nausea or vomiting, and what was "
        "brought up; change in bowel habit, and anything unusual in the stool; "
        "appetite and unintended weight change; heartburn or reflux; difficulty "
        "or pain swallowing; foods, alcohol or medicines that set it off; "
        "anti-inflammatory or painkiller use"
    ),
    PrescreeningCategory.GENERAL_CHECKUP: (
        "what prompted them to book a checkup now, and whether anything "
        "specific is on their mind; when they were last seen and what was "
        "checked then; any symptom they have been putting off mentioning; "
        "energy, sleep, appetite and weight over recent months; mood and "
        "stress; exercise and diet; tobacco and alcohol; medications and "
        "supplements they take regularly; screening or vaccinations they know "
        "are due; anything in the family that worries them"
    ),
    PrescreeningCategory.NOT_SURE: (
        "an open description of what feels wrong, in their own words, before "
        "anything else; when it started and whether it came on suddenly or "
        "gradually; where in the body they notice it most; what makes it better "
        "or worse and what they were doing when it began; whether it is there "
        "all the time or comes in episodes; changes in sleep, appetite, weight, "
        "energy or mood alongside it; whether anything like it has happened "
        "before; what they have already tried; what worries them most about it"
    ),
}
"""What a pre-screening for each appointment reason should end up covering.

Coverage areas, deliberately not questions. The live agent writes its own
questions on the spot from the patient's answers (see
`app.agents.tools.symptoms`); this is the checklist it writes them
*against*, so a diabetes booking cannot drift into a generic pain
interview and a "not sure" booking is no longer a blank page.

Exhaustive over `PrescreeningCategory` on purpose -- a reason a patient
can book under with no brief here is a reason the call has nothing
specific to ask about, which is the bug this exists to close.

**Only half a brief on its own.** Every entry here is written for a
*condition unfolding over time*, because the booking vocabulary it is
keyed on is a list of chronic organ-system concerns. Pair it with
`PRESENTATION_BRIEFS` below, which supplies the other axis -- how the
problem presented -- and which overrides this one where the two disagree.
"""


class PresentationType(StrEnum):
    """How the patient's problem actually presented, independent of which
    body system it belongs to.

    The missing axis, and the reason a real call asked a patient with a
    torn hamstring whether the tear "comes and goes".

    `PrescreeningCategory` answers *what area is this about*. It is a
    booking menu of chronic conditions plus two catch-alls, so a discrete
    traumatic injury has nowhere to go in it and lands on `NOT_SURE` --
    whose brief, correctly for an unexplained symptom and absurdly for a
    torn muscle, asks whether it is constant or comes in episodes, whether
    anything like it has happened before, and what the patient has already
    tried.

    Nothing was wrong with that brief. What was wrong was having only one
    axis. A muscle tear while playing football is not an unplaceable
    complaint -- the patient knows exactly what happened, when, and doing
    what. The useful questions are about mechanism, whether they could
    carry on playing, whether they felt a pop, swelling and bruising. None
    of those are expressible as a point on the organ-system axis.

    The model chooses this from the conversation, the same way it chooses
    the category. It is not inferred from keywords anywhere.
    """

    ACUTE_INJURY = "acute_injury"
    """A discrete event injured them: a tear, sprain, fall, blow, burn, cut."""

    NEW_PROBLEM = "new_problem"
    """A new symptom that came on without an injury behind it."""

    ONGOING_CONDITION = "ongoing_condition"
    """A condition they already know they have, being reviewed or managed."""

    ROUTINE = "routine"
    """No complaint at all -- a checkup, a screening, a follow-up on nothing."""

    UNDIFFERENTIATED = "undifferentiated"
    """Something is wrong and they genuinely cannot place it."""


PRESENTATION_BRIEFS: dict[PresentationType, str] = {
    PresentationType.ACUTE_INJURY: (
        "exactly what happened and what they were doing at the moment it "
        "happened; whether they felt or heard anything at the time -- a pop, a "
        "snap, a tearing feeling; whether they could carry on, or had to stop "
        "immediately; whether they can put weight on it or use it normally now; "
        "swelling, bruising, or any visible change in shape; where exactly it "
        "hurts and whether that has moved or spread since; what has happened "
        "over the hours or days since -- better, worse, or the same; what they "
        "have already done for it, including ice, strapping, rest or painkillers; "
        "whether they have injured the same place before; what it is stopping "
        "them doing now"
    ),
    PresentationType.NEW_PROBLEM: (
        "when it started and whether it came on suddenly or gradually; what it "
        "feels like in their own words; whether it is there all the time or comes "
        "in episodes, and how long an episode lasts; what makes it better or "
        "worse; anything that was going on when it began; whether anything like "
        "it has happened before; what they have already tried; what it is "
        "stopping them doing; what worries them most about it"
    ),
    PresentationType.ONGOING_CONDITION: (
        "how long they have had it and how it is normally kept under control; "
        "what has changed recently and when; how well the current treatment is "
        "working, and whether they manage to take it as prescribed; any side "
        "effects; readings or measurements they take at home, if they take any; "
        "what prompted this appointment specifically; anything new alongside the "
        "usual pattern"
    ),
    PresentationType.ROUTINE: (
        "what prompted them to book now, and whether anything specific is on "
        "their mind; when they were last seen and what was checked then; any "
        "symptom they have been putting off mentioning; how they have been "
        "feeling generally over recent months; screening or vaccinations they "
        "know are due; anything in the family that worries them"
    ),
    PresentationType.UNDIFFERENTIATED: (
        "an open description of what feels wrong, in their own words, before "
        "anything else; when it started and whether it came on suddenly or "
        "gradually; where in the body they notice it most; what makes it better "
        "or worse; whether it is there all the time or comes in episodes; "
        "changes in sleep, appetite, weight, energy or mood alongside it; "
        "whether anything like it has happened before; what they have already "
        "tried; what worries them most about it"
    ),
}
"""What to cover given *how* the problem presented, whatever area it is in.

Leads the category brief rather than supplementing it. Where the two
disagree, this one is right: the category brief describes the natural
history of a chronic condition, and half of that is nonsense asked about
an event that happened once, at a known moment, doing a known thing.

Exhaustive over `PresentationType`, for the same reason
`PRESCREENING_CATEGORY_BRIEFS` is exhaustive over its own key.
"""


PRESENTATION_CAUTIONS: dict[PresentationType, str] = {
    PresentationType.ACUTE_INJURY: (
        "This is a single event with a known cause, not a condition with a "
        "pattern. Do NOT ask whether it comes and goes, whether it is constant "
        "or intermittent, whether they have had episodes, or what triggers it -- "
        "they have already told you what caused it. Do not ask them to place it "
        "in a body system, and do not screen it as a long-term condition."
    ),
    PresentationType.NEW_PROBLEM: (
        "They do not yet know what this is. Do not use a condition name back at "
        "them, and do not ask questions that assume a diagnosis."
    ),
    PresentationType.ONGOING_CONDITION: (
        "They live with this and have answered the basics many times. Do not "
        "ask them to explain the condition itself or when they were diagnosed "
        "unless it is genuinely unclear -- ask what has changed."
    ),
    PresentationType.ROUTINE: (
        "There is no complaint. Do not invent one, do not ask what their "
        "symptoms are, and do not press for a problem they have not raised."
    ),
    PresentationType.UNDIFFERENTIATED: (
        "They cannot name it, so do not ask them to. Let them describe it "
        "first, in full, before narrowing anything down."
    ),
}
"""The questions each presentation makes absurd, stated as prohibitions.

Separate from the brief because they are a different kind of instruction:
the brief says what to find out, this says what asking would reveal you had
not listened. "Does the tear come and go?" is the exact line this exists to
stop, and a positive brief alone never stopped it -- the model reached for
the pattern questions because `PRESCREENING_CATEGORY_BRIEFS[NOT_SURE]`
genuinely asks for them.
"""


_PRESENTATION_ALIASES: dict[str, PresentationType] = {
    "injury": PresentationType.ACUTE_INJURY,
    "acute injury": PresentationType.ACUTE_INJURY,
    "trauma": PresentationType.ACUTE_INJURY,
    "traumatic": PresentationType.ACUTE_INJURY,
    "accident": PresentationType.ACUTE_INJURY,
    "acute": PresentationType.ACUTE_INJURY,
    "new": PresentationType.NEW_PROBLEM,
    "new symptom": PresentationType.NEW_PROBLEM,
    "new complaint": PresentationType.NEW_PROBLEM,
    "chronic": PresentationType.ONGOING_CONDITION,
    "ongoing": PresentationType.ONGOING_CONDITION,
    "existing": PresentationType.ONGOING_CONDITION,
    "known condition": PresentationType.ONGOING_CONDITION,
    "review": PresentationType.ONGOING_CONDITION,
    "follow up": PresentationType.ONGOING_CONDITION,
    "checkup": PresentationType.ROUTINE,
    "check up": PresentationType.ROUTINE,
    "screening": PresentationType.ROUTINE,
    "wellness": PresentationType.ROUTINE,
    "preventive": PresentationType.ROUTINE,
    "unclear": PresentationType.UNDIFFERENTIATED,
    "unknown": PresentationType.UNDIFFERENTIATED,
    "not sure": PresentationType.UNDIFFERENTIATED,
    "unplaceable": PresentationType.UNDIFFERENTIATED,
}
"""Ways the model spells a presentation type. A spelling table, like
`_CATEGORY_ALIASES`, and just as deliberately not a classifier."""


def resolve_presentation_type(value: str | None) -> PresentationType | None:
    """Resolve a presentation type the model supplied, or None if unusable.

    Whole-string match only -- no substring scanning. A presentation type
    is something the model decides from the conversation and names; it is
    never something to recover from the patient's words by pattern.
    """
    if not value:
        return None
    cleaned = " ".join(value.strip().lower().replace("-", " ").replace("_", " ").split())
    if not cleaned:
        return None
    for candidate in PresentationType:
        if cleaned == candidate.value.replace("_", " "):
            return candidate
    return _PRESENTATION_ALIASES.get(cleaned)


def default_presentation_for(category: PrescreeningCategory) -> PresentationType:
    """The presentation a booking reason implies when the model did not say.

    A fallback, not a classification. The model is asked for the
    presentation explicitly and normally gives one; this keeps a screening
    usable when it does not, by reading the only signal the booking itself
    carries.
    """
    if category is PrescreeningCategory.GENERAL_CHECKUP:
        return PresentationType.ROUTINE
    if category is PrescreeningCategory.NOT_SURE:
        return PresentationType.UNDIFFERENTIATED
    # The five condition reasons are things a patient books under because
    # they already know they have them.
    return PresentationType.ONGOING_CONDITION


_CATEGORY_ALIASES: dict[str, PrescreeningCategory] = {
    alias: category
    for category, aliases in {
        PrescreeningCategory.DIABETES: (
            "diabetes",
            "diabetic",
            "blood sugar",
            "blood sugars",
        ),
        PrescreeningCategory.BLOOD_PRESSURE: (
            "blood pressure",
            "hypertension",
            "hypertensive",
            "bp",
        ),
        PrescreeningCategory.HEART: (
            "heart",
            "cardiac",
            "cardiology",
            "palpitations",
        ),
        PrescreeningCategory.LUNG: (
            "lung",
            "lungs",
            "breathing",
            "respiratory",
            "asthma",
            "copd",
        ),
        PrescreeningCategory.STOMACH: (
            "stomach",
            "digestion",
            "digestive",
            "gastro",
            "gastrointestinal",
            "abdominal",
            "bowel",
        ),
        PrescreeningCategory.GENERAL_CHECKUP: (
            "general checkup",
            "general check up",
            "checkup",
            "check up",
            "routine",
            "wellness",
            "physical",
            "annual",
        ),
        PrescreeningCategory.NOT_SURE: (
            "not sure",
            "notsure",
            "unsure",
            "unplaceable",
            "undifferentiated",
        ),
    }.items()
    for alias in aliases
}
"""Ways the model spells one of the seven values, resolved onto the vocabulary.

This is a **spelling table, not a classifier.** Every entry here is a name
for an appointment reason. None of them is a *symptom*, and that is the
whole correction: the table used to carry "chest", "cough", "breath",
"sugar", "gut" and "other", which turned this function into a keyword
triage engine sitting underneath the one component actually capable of
understanding a sentence.

What that cost, concretely. "Chest infection" contains "chest", so it
resolved to `heart` and a respiratory illness was screened as a cardiac
complaint. "Other" appears inside "bothered", "mother" and "brother", so a
patient describing their mother's diabetes was filed as unable to place
what was wrong. Both were silent: the tool reported success and the model
had no way to know it had been overruled.

Deciding which reason a patient's story belongs to is the model's job --
it has the whole conversation, and it is told to make that call in
`start_prescreening`. When a value does not resolve, the right answer is
to say so and let the model choose again, never to guess from a substring.
"""


def resolve_prescreening_category(value: str | None) -> PrescreeningCategory | None:
    """Resolve a category name the model supplied onto the vocabulary.

    The live agent is told the seven exact values and normally sends one,
    but a voice model mid-conversation will occasionally send a label
    ("Blood pressure") or a synonym ("hypertension"). Those resolve.

    A sentence made out of the *patient's* words does not, and must not.
    Matching is on whole words against a name for a reason, never on a
    substring of a symptom: the previous version searched every alias
    inside the supplied text, so "chest infection" became `heart` and any
    string containing "other" became `not_sure`.

    Returns `None` for anything else, which the tool reports back as a
    correction rather than raising -- an exception here surfaces to the
    patient as a dead turn mid-sentence, and a guess surfaces as the wrong
    screening for the rest of the call.
    """
    if not value:
        return None
    cleaned = " ".join(value.strip().lower().replace("-", " ").replace("_", " ").split())
    if not cleaned:
        return None
    for candidate in PrescreeningCategory:
        if cleaned == candidate.value.replace("_", " "):
            return candidate
    direct = _CATEGORY_ALIASES.get(cleaned)
    if direct is not None:
        return direct

    # Whole-word containment, longest alias first, so "blood pressure" is
    # not shadowed by "bp" and "general checkup" is not shadowed by
    # "checkup". Word-bounded so "other" cannot match inside "bothered" --
    # a bare substring scan is what made this a classifier.
    words = cleaned.split()
    for alias in sorted(_CATEGORY_ALIASES, key=len, reverse=True):
        alias_words = alias.split()
        span = len(alias_words)
        if span > len(words):
            continue
        for start in range(len(words) - span + 1):
            if words[start : start + span] == alias_words:
                return _CATEGORY_ALIASES[alias]
    return None


def prescreening_category_label(category: PrescreeningCategory) -> str:
    """The patient-facing label for a category, worded as the booking screen showed it.

    An identifier for logs, prompts and the physician's report -- never
    something to say to the patient. `prescreening_category_topic` is what
    goes inside a spoken sentence.
    """
    return BOOKING_VISIT_TYPES[category].label


def prescreening_category_topic(category: PrescreeningCategory) -> str | None:
    """How to name this reason out loud, or None when it cannot be named.

    `None` for `NOT_SURE` only, and it is a meaningful answer rather than
    missing data: the patient declared at booking that they could not place
    what was wrong, so there is no topic to confirm back to them.
    """
    return BOOKING_VISIT_TYPES[category].spoken_topic


def is_unscoped_reason(category: PrescreeningCategory) -> bool:
    """Whether this booking reason names no topic the call can open by confirming."""
    return BOOKING_VISIT_TYPES[category].spoken_topic is None


def prescreening_category_reference(category: PrescreeningCategory) -> str:
    """How to name a reason inside text written *to the agent*.

    Three vocabularies, because they have three different readers, and
    collapsing them is what put "You've booked this appointment about I am
    not sure" in a patient's ear:

    - `prescreening_category_label` -- the button the patient tapped.
      Correct on the booking form, correct in a log, correct on the
      physician's report. Never speech.
    - `prescreening_category_topic` -- how a person says it in a sentence.
      The only one that may be spoken.
    - this -- how the tool layer refers to it when talking to the model.

    `NOT_SURE` is the whole reason this exists. Its label is written in
    the first person, so a tool reply saying "Screening: I am not sure"
    reads as something to say rather than something to do, and a
    speech-to-speech model under time pressure says it.
    """
    if category is PrescreeningCategory.NOT_SURE:
        return "a complaint the patient cannot place"
    return BOOKING_VISIT_TYPES[category].label
