"""What the symptom-story screen collected, one answered question at a time.

Separate from `Session.collected_details` -- which is a flat
screen/field/value map -- because the symptom conversation is not a fixed
set of fields. It is an ordered sequence of question-and-answer pairs the
agent chose from the question bank based on what the patient said, and it
has to survive a reconnect and reach the physician's report in that shape.
`PreScreeningReport.Finding` is the same shape for the same reason.
"""

from pydantic import BaseModel, Field

from app.core.constants import ANSWERED_STATUSES, AnswerStatus, PrescreeningCategory


class SymptomAnswer(BaseModel):
    """One screening question and what the patient said to it."""

    question_id: str
    category: PrescreeningCategory
    question: str = Field(description="As it was actually asked, not necessarily the seeded text")
    answer: str = Field(description="The patient's own words -- never normalized or inferred")
    status: AnswerStatus = Field(
        default=AnswerStatus.CONFIRMED,
        description=(
            "How well this answer is actually known. A screening answer used "
            "to be a bare string, so 'about three weeks', 'maybe three weeks?', "
            "'I'd rather not go into it' and 'I honestly don't know' all "
            "reached the physician's report as equally firm findings, and all "
            "four counted identically toward the question quota. Defaults to "
            "`confirmed` so answers stored before this field existed keep "
            "exactly the meaning they were given at the time."
        ),
    )

    @property
    def is_answered(self) -> bool:
        """Whether the patient actually engaged with the question.

        True for a decline and for an honest "I don't know": both are the
        patient answering, and both are worth more to the physician than a
        value they were pressed into. False only for `inferred`, which is
        the agent's own reading and never a substitute for asking.
        """
        return self.status in ANSWERED_STATUSES
