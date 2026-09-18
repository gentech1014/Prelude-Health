"""The `session_chat_history` domain model.

One document per patient visit, keyed by `session_id` — never by patient
name, since a patient can have multiple sessions over time and a name is
not a stable, collision-free identifier.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.core.constants import (
    BookingVisitType,
    IntakeScreen,
    PrescreeningCategory,
    PresentationType,
    SessionState,
    Sex,
)
from app.models.consent import Consent
from app.models.report import PreScreeningReport
from app.models.symptom_intake import SymptomAnswer
from app.models.transcript import ConversationTurn


class PatientRef(BaseModel):
    """Minimal patient identity carried on a session, not used as its key."""

    name: str
    patient_id: str
    date_of_birth: datetime = Field(
        description=(
            "Midnight UTC on the patient's birth date, not a plain `date` -- "
            "BSON has no date-only type, so pymongo's encoder raises "
            "`InvalidDocument` on a bare `datetime.date`. The API layer "
            "(`BookingConfirmedWebhook`, `ScheduleAppointmentRequest`) still "
            "accepts a plain date; `SessionService` converts it once."
        )
    )
    sex: Sex
    contact_phone: str | None = Field(
        default=None,
        description=(
            "The number the booking gave, which is where the intake link was "
            "sent. Kept so the confirm-details screen can show the patient "
            "what the clinic holds for them rather than asking them to type it "
            "again -- it is the one identity field on that screen the call is "
            "allowed to correct. None for a booking that supplied no number."
        ),
    )


class Session(BaseModel):
    """A single pre-screening session, from booking through report delivery."""

    session_id: str
    appointment_id: str
    patient: PatientRef
    physician: str
    appointment_datetime: datetime
    appointment_timezone: str = Field(
        default="UTC",
        description=(
            "IANA zone the appointment time is spoken/rendered in, resolved "
            "once at booking (Doctor.timezone, else Settings.clinic_timezone) "
            "and never re-looked-up -- a later change to either must not "
            "retroactively re-render an already-booked session. Defaults to "
            "'UTC' only for sessions written before this field existed; that "
            "preserves their prior (already-ambiguous) display rather than "
            "guessing an intended zone for data that never recorded one."
        ),
    )
    current_slot_id: str | None = Field(
        default=None, description="The appointment_slots document this session currently holds."
    )
    cancelled_at: datetime | None = None
    cancellation_reason: str | None = Field(
        default=None, description="Why the patient cancelled, in their own words, if given."
    )
    booking_visit_type: BookingVisitType | None = Field(
        default=None,
        description=(
            "The appointment reason the patient actually selected when booking, "
            "kept structurally rather than only as words inside `booking_reason`. "
            "This is what the call opens by confirming, and what the screening is "
            "organized around when they confirm it -- `PrescreeningCategory` is "
            "this same vocabulary. None for a booking that came in without one, "
            "and for sessions created before this field existed; the call then "
            "opens with an open question instead of a confirmation."
        ),
    )
    booking_reason: str | None = Field(
        default=None,
        description=(
            "The free-text reason captured at booking, if the platform supplied "
            "one. Colour for the agent's opening question and for the report -- "
            "never treated as the patient's actual concern, which the call itself "
            "establishes. The selected visit type is `booking_visit_type`; older "
            "sessions have it folded into this string instead."
        ),
    )
    status: SessionState = SessionState.BOOKING_CREATED
    consent: Consent | None = Field(
        default=None, description="Must be recorded, and given=True, before the live call may start"
    )
    turns: list[ConversationTurn] = Field(default_factory=list)
    last_screen: IntakeScreen | None = Field(
        default=None,
        description=(
            "The screen the live call had reached, written by the agent's "
            "`navigate_to_screen` tool. This is the resume marker: without it a "
            "patient whose connection dropped rejoins at the first question."
        ),
    )
    collected_details: dict[str, dict[str, str]] = Field(
        default_factory=dict,
        description=(
            "What the agent heard, keyed by screen then by field, in the "
            "patient's own words. Prefill for the patient's screens, resume "
            "context for a rebuilt agent, and corroboration for the "
            "summarizer -- never a second source of truth: the transcript "
            "leads, and where the two disagree the transcript wins."
        ),
    )
    collected_statuses: dict[str, dict[str, str]] = Field(
        default_factory=dict,
        description=(
            "How well each value in `collected_details` is actually known, "
            "keyed identically, as `AnswerStatus` values -- confirmed, "
            "uncertain, inferred, undisclosed or unknown. Its own map rather "
            "than a richer `collected_details` so every existing reader of "
            "that field is untouched; a session written before this existed "
            "simply has none, and anything missing here reads as confirmed, "
            "which is exactly how those values were treated at the time. "
            "This is what lets the physician see that a patient declined to "
            "discuss their drinking rather than that nobody asked."
        ),
    )
    collected_selections: dict[str, dict[str, str]] = Field(
        default_factory=dict,
        description=(
            "Which on-screen options the agent picked, keyed by screen then by "
            "field, as comma-joined option ids from `SCREEN_FIELD_OPTIONS`. "
            "Purely presentational: it is what lets a reconnect restore a "
            "ticked card rather than re-deriving it by matching text, and it "
            "is deliberately withheld from the summarizer, which reads "
            "`collected_details` because the patient's words are the record."
        ),
    )
    symptom_categories: list[PrescreeningCategory] = Field(
        default_factory=list,
        description=(
            "The appointment reasons this call actually screened for -- normally "
            "just the one the patient confirmed from their booking, and a second "
            "only when they raised something separate alongside it. Same "
            "vocabulary as `booking_visit_type`, so the two are directly "
            "comparable: where they differ, the patient said the booking was not "
            "what they were really there for."
        ),
    )
    symptom_presentation: PresentationType | None = Field(
        default=None,
        description=(
            "How the patient's problem presented -- a discrete injury, a new "
            "symptom, a known condition, a routine visit, or something they "
            "cannot place. Stored alongside `symptom_categories` because it "
            "decides what is worth asking at least as much as the body area "
            "does, and losing it on a reconnect would let a resumed call go "
            "back to asking a torn muscle whether it comes and goes. None for "
            "a call that never got as far as screening, and for sessions "
            "written before this field existed."
        ),
    )
    symptom_answers: list[SymptomAnswer] = Field(
        default_factory=list,
        description=(
            "The symptom screen's question-and-answer record, in the order it "
            "was collected. Kept apart from `collected_details` because that "
            "screen has no fixed fields -- the questions are chosen mid-call "
            "from the seeded bank. Resume context for a rebuilt agent, and the "
            "detailed record behind the report's findings."
        ),
    )
    document_uploaded: bool = False
    document_ref: str | None = Field(
        default=None,
        description=(
            "Object storage key for an uploaded supporting document, if any -- "
            "the raw key, not a `s3://...` URI, so a presigned download URL "
            "can be regenerated for it later (e.g. to reference it in the "
            "physician PDF report)."
        ),
    )
    document_summary: str | None = Field(
        default=None,
        description=(
            "One or two sentences stating the uploaded document's TYPE only "
            "(e.g. 'This appears to be an X-ray image.') -- never its "
            "contents or findings, per `describe_document`'s prompt. Lives "
            "here, not on `PreScreeningReport`, because that model is "
            "deliberately scoped to only what the transcript itself says; "
            "see its docstring."
        ),
    )
    report: PreScreeningReport | None = None
    summary_error: str | None = Field(
        default=None,
        description=(
            "Why summarization failed, when status is SUMMARY_FAILED. Present so "
            "the physician view can explain the absence of a report rather than "
            "rendering a blank one."
        ),
    )
    video_ref: str | None = Field(
        default=None, description="Object storage URI for the screen recording"
    )
    calendar_event_id: str | None = Field(
        default=None,
        description=(
            "Google Calendar event id created for this appointment by the mock "
            "booking platform. Empty string means Calendar delivery was not "
            "configured at booking time; None means the session predates this "
            "field or was not created through the mock booking platform. "
            "Either way, delivery is skipped rather than attempted."
        ),
    )
    created_at: datetime
    updated_at: datetime
