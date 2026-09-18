"""The structured output produced by the summarization agent.

Passed to the summarizer via `structured_output_model=PreScreeningReport`
(the current, non-deprecated Strands API — not `agent.structured_output(...)`,
which still appears in some provider doc pages but is deprecated).
"""

from pydantic import BaseModel, Field

from app.core.constants import PrescreeningCategory


class Finding(BaseModel):
    """One answered question from the intake conversation."""

    question: str
    answer: str
    flagged: bool = Field(
        default=False, description="True if this answer warrants closer physician attention"
    )


class Medication(BaseModel):
    """One medication the patient said they currently take."""

    name: str
    dose: str | None = Field(default=None, description="Only if the patient actually gave one")
    frequency: str | None = Field(default=None, description="Only if the patient actually gave one")
    reason: str | None = Field(default=None, description="Why they take it, only if stated")


class Allergy(BaseModel):
    """One medication allergy the patient reported."""

    allergen: str
    reaction: str | None = Field(default=None, description="Only if the patient described one")


class HospitalStay(BaseModel):
    """One hospital admission the patient mentioned."""

    reason: str
    timeframe: str | None = Field(default=None, description="Only if the patient gave one")


class RecentCare(BaseModel):
    """Another clinician the patient saw about this recently."""

    who: str = Field(description="Who they saw, in their own words -- a name, or 'a specialist'")
    when: str | None = Field(default=None, description="Only if the patient gave one")
    reason: str | None = Field(default=None, description="Only if the patient said")


class SocialHistory(BaseModel):
    """Tobacco, alcohol, work and home, only as far as the patient answered.

    Every field is optional and every one stays null unless the patient
    actually said it. A patient who declines to discuss alcohol produces a
    null here and a line in `gaps` -- never an assumed "none".
    """

    tobacco_use: str | None = None
    alcohol_use: str | None = None
    occupation: str | None = None
    living_situation: str | None = None


class PreScreeningReport(BaseModel):
    """Physician-facing summary of a completed pre-screening call.

    Deliberately carries no `document_uploaded` flag: the summarization
    agent sees only the transcript and cannot know whether an upload
    actually landed in object storage. That fact lives on the session
    document, written by the upload endpoint, which does know.
    """

    chief_concern: str = Field(description="One-line summary of why the patient is visiting")
    category: PrescreeningCategory | None = Field(
        default=None,
        description=(
            "The appointment reason this visit was screened under -- the same "
            "vocabulary the patient booked with, so a routine checkup and an "
            "unplaceable complaint are real answers here rather than gaps. Null "
            "only when the call never established one at all, which a completed "
            "call should not produce; picking the nearest condition instead "
            "would put an invented clinical label at the top of the report."
        ),
    )
    clinical_summary: str = Field(
        description=(
            "A 3-6 sentence narrative, in the plain clinical shorthand a "
            "physician already reads daily, organizing what the patient "
            "reported into prose -- not a diagnosis, not an interpretation, "
            "just their own facts in a form that reads in under 15 seconds. "
            "This is the report's primary surface; `findings` below it is "
            "the detailed Q&A record this summary was drawn from."
        )
    )
    findings: list[Finding] = Field(default_factory=list)
    medications: list[Medication] = Field(
        default_factory=list,
        description=(
            "Medications the patient said they currently take. Empty means the "
            "patient said they take none -- if they were never asked or declined "
            "to answer, that belongs in `gaps`, not a silently empty list."
        ),
    )
    allergies: list[Allergy] = Field(
        default_factory=list,
        description=(
            "Medication allergies the patient reported. Empty means the patient "
            "said they have none -- if never asked or declined, that belongs in "
            "`gaps`, not a silently empty list."
        ),
    )
    ongoing_conditions: list[str] = Field(
        default_factory=list,
        description=(
            "Conditions the patient said they are currently being treated for. "
            "Empty means they said none -- if never asked or declined, that "
            "belongs in `gaps`, not a silently empty list."
        ),
    )
    hospital_stays: list[HospitalStay] = Field(
        default_factory=list,
        description="Admissions the patient mentioned. Same empty-versus-gap rule as above.",
    )
    recent_care: list[RecentCare] = Field(
        default_factory=list,
        description=(
            "Other clinicians the patient saw about this recently. Same "
            "empty-versus-gap rule as above."
        ),
    )
    recent_tests: str | None = Field(
        default=None,
        description=(
            "Tests or results the patient mentioned, in their own words. Null if "
            "they said there were none, were never asked, or declined -- the last "
            "two also belong in `gaps`."
        ),
    )
    family_history: list[str] = Field(
        default_factory=list,
        description=(
            "Conditions the patient reported in immediate family, each with the "
            "relative if they named one. Same empty-versus-gap rule as above."
        ),
    )
    social_history: SocialHistory = Field(
        default_factory=SocialHistory,
        description="Tobacco, alcohol, work and home, only as far as the patient answered.",
    )
    patient_goal: str | None = Field(
        default=None,
        description="What the patient said they want from today's visit, in their own words.",
    )
    additional_notes: str | None = Field(
        default=None,
        description=(
            "Anything else the patient asked to pass on at the end of the call, "
            "in their own words. Null if they raised nothing."
        ),
    )
    gaps: list[str] = Field(
        default_factory=list,
        description=(
            "Questions the patient could not answer, declined, or that were never asked. "
            "An honest gap is more useful to the physician than a guessed value."
        ),
    )
