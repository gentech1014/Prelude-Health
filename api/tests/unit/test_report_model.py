"""Unit tests for the `PreScreeningReport` domain model.

The `gaps` field is the one BA-mandated field most likely to be dropped by
accident (US-11) -- these tests pin its presence and default behavior.
"""

from app.core.constants import SymptomCategory
from app.models.report import Allergy, Finding, Medication, PreScreeningReport


def test_report_defaults_to_empty_gaps_and_findings() -> None:
    """A report with no findings/gaps supplied still has valid (empty) lists, not None."""
    report = PreScreeningReport(
        chief_concern="Shortness of breath on exertion",
        category=SymptomCategory.LUNG,
        clinical_summary="Reports exertional dyspnea; no further detail captured.",
    )

    assert report.findings == []
    assert report.gaps == []


def test_report_defaults_to_empty_medications_and_allergies_and_no_goal() -> None:
    """Empty lists (not None) when omitted, same as `findings`/`gaps` -- and
    `patient_goal` defaults to None rather than an empty string, so a
    genuinely unstated goal is distinguishable from one the patient stated
    as blank."""
    report = PreScreeningReport(
        chief_concern="Shortness of breath on exertion",
        category=SymptomCategory.LUNG,
        clinical_summary="Reports exertional dyspnea; no further detail captured.",
    )

    assert report.medications == []
    assert report.allergies == []
    assert report.patient_goal is None


def test_medication_only_requires_a_name() -> None:
    """Dose, frequency, and reason must stay optional -- the patient may not
    know or state any of them, and nothing should be guessed to fill them in."""
    medication = Medication(name="Ibuprofen")

    assert medication.dose is None
    assert medication.frequency is None
    assert medication.reason is None


def test_allergy_only_requires_an_allergen() -> None:
    """The reaction must stay optional -- the patient may not describe one."""
    allergy = Allergy(allergen="Penicillin")

    assert allergy.reaction is None


def test_report_carries_medications_allergies_and_patient_goal() -> None:
    """All three populate independently of `findings`/`gaps`."""
    report = PreScreeningReport(
        chief_concern="Shortness of breath on exertion",
        category=SymptomCategory.LUNG,
        clinical_summary="Reports exertional dyspnea; no further detail captured.",
        medications=[Medication(name="Albuterol", frequency="as needed")],
        allergies=[Allergy(allergen="Penicillin", reaction="Rash")],
        patient_goal="Understand what's causing the breathlessness",
    )

    assert report.medications == [Medication(name="Albuterol", frequency="as needed")]
    assert report.allergies == [Allergy(allergen="Penicillin", reaction="Rash")]
    assert report.patient_goal == "Understand what's causing the breathlessness"


def test_report_carries_unanswered_questions_as_gaps() -> None:
    """Unanswered questions must show up in `gaps`, distinct from answered `findings`."""
    report = PreScreeningReport(
        chief_concern="Shortness of breath on exertion",
        category=SymptomCategory.LUNG,
        clinical_summary="Reports exertional dyspnea for two weeks. Smoking history not confirmed.",
        findings=[Finding(question="How long?", answer="Two weeks", flagged=False)],
        gaps=["Any history of smoking?"],
    )

    assert len(report.findings) == 1
    assert report.gaps == ["Any history of smoking?"]


def test_report_has_no_document_uploaded_field() -> None:
    """`document_uploaded` belongs to the session, not the report.

    The summarizer only ever sees a transcript, so it cannot know whether
    an upload actually reached storage -- the agent can offer an upload the
    patient never completes. Pinning its absence stops it being
    reintroduced here and quietly disagreeing with the session document.
    """
    assert "document_uploaded" not in PreScreeningReport.model_fields


def test_report_has_no_field_capable_of_holding_an_assessment() -> None:
    """The schema is one of three places the no-diagnosis rule is enforced.

    Adding a field like `diagnosis` or `triage_level` would let a model put a
    clinical judgement in front of a physician as though the service had made
    one. If this test fails, that boundary was crossed.
    """
    forbidden = {"diagnosis", "assessment", "triage", "triage_level", "severity", "recommendation"}

    assert forbidden.isdisjoint(PreScreeningReport.model_fields)


def test_a_report_can_have_no_category() -> None:
    """Null is the honest answer for a concern outside the five screened areas.

    A required category forced the model to pick the nearest-sounding one,
    which put an invented clinical label at the top of a routine-checkup
    report.
    """
    report = PreScreeningReport(
        chief_concern="Routine checkup",
        clinical_summary="Here for a general checkup. No symptoms reported.",
    )

    assert report.category is None
