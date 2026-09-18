"""Unit tests for the physician report PDF renderer.

Content now flows continuously with no forced page breaks (see the
module docstring in `app.services.report_pdf`), so the exact page count
is a function of content length, not something these tests can predict
reliably -- they check structural correctness instead: a valid PDF comes
out, and the detail section's presence/absence actually changes the
output.
"""

import re
from datetime import UTC, date, datetime
from io import BytesIO

import pytest
from pypdf import PdfReader

from app.agents.summarizer.agent import DESCRIPTION_UNAVAILABLE
from app.core.constants import Sex, SymptomCategory
from app.models.report import Allergy, Finding, Medication, PreScreeningReport
from app.models.session import PatientRef, Session
from app.services.report_pdf import (
    _age_years,
    _format_appointment,
    render_report_html,
    render_report_pdf,
)


def _session(
    *,
    with_report: bool,
    with_findings: bool = True,
    medications: list[Medication] | None = None,
    allergies: list[Allergy] | None = None,
    patient_goal: str | None = None,
    category: SymptomCategory | None = SymptomCategory.LUNG,
    document_uploaded: bool = False,
    document_summary: str | None = None,
) -> Session:
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    report = (
        PreScreeningReport(
            chief_concern="Shortness of breath on exertion",
            category=category,
            clinical_summary=(
                "Reports gradually worsening exertional dyspnea over 3 weeks, with "
                "intermittent right-sided chest pain. No fever."
            ),
            findings=(
                [
                    Finding(
                        question="How long has this gone on?", answer="About 3 weeks", flagged=False
                    ),
                    Finding(question="Any chest pain?", answer="Yes, sharp pain", flagged=True),
                ]
                if with_findings
                else []
            ),
            medications=medications or [],
            allergies=allergies or [],
            patient_goal=patient_goal,
            gaps=["Smoking history not confirmed"],
        )
        if with_report
        else None
    )
    return Session(
        session_id="sess_pdf_test",
        appointment_id="appt_pdf_test",
        patient=PatientRef(
            name="Asha Rao",
            patient_id="pt_2290",
            date_of_birth=datetime(1990, 5, 14, tzinfo=UTC),
            sex=Sex.FEMALE,
        ),
        physician="Dr. Mehta",
        appointment_datetime=datetime(2026, 9, 3, 9, 30, tzinfo=UTC),
        report=report,
        document_uploaded=document_uploaded,
        document_summary=document_summary,
        created_at=now,
        updated_at=now,
    )


def test_render_report_pdf_produces_a_valid_pdf() -> None:
    """The output is a real, well-formed PDF."""
    pdf_bytes = render_report_pdf(_session(with_report=True))

    assert pdf_bytes.startswith(b"%PDF-")
    assert pdf_bytes.rstrip().endswith(b"%%EOF")


def test_findings_present_produce_more_content_than_findings_absent() -> None:
    """The detail section (one card per finding) actually adds to the document.

    A precise page-break assertion would be fragile now that content flows
    continuously with no forced breaks -- byte length is a simpler, still
    meaningful proxy for "the extra section was actually rendered."
    """
    with_findings = render_report_pdf(_session(with_report=True, with_findings=True))
    without_findings = render_report_pdf(_session(with_report=True, with_findings=False))

    assert len(with_findings) > len(without_findings)


def test_medications_present_produce_more_content_than_absent() -> None:
    """The MEDICATIONS section actually adds to the document, same byte-length proxy."""
    with_medications = render_report_pdf(
        _session(
            with_report=True, medications=[Medication(name="Albuterol", frequency="as needed")]
        )
    )
    without_medications = render_report_pdf(_session(with_report=True))

    assert len(with_medications) > len(without_medications)


def test_allergies_present_produce_more_content_than_absent() -> None:
    """The ALLERGIES section actually adds to the document, same byte-length proxy."""
    with_allergies = render_report_pdf(
        _session(with_report=True, allergies=[Allergy(allergen="Penicillin", reaction="Rash")])
    )
    without_allergies = render_report_pdf(_session(with_report=True))

    assert len(with_allergies) > len(without_allergies)


def test_patient_goal_renders_the_patients_own_words_or_says_it_was_not_stated() -> None:
    """The goal field carries the patient's actual words, not just the fallback.

    Asserted on the HTML rather than by comparing PDF byte lengths. That
    proxy was always weak and the uniform value boxes made it weaker: both
    states now render a box of the same size, so the only difference left
    is the text inside it -- which is the thing worth asserting anyway.
    """
    # No apostrophe: the environment autoescapes, correctly, and this test
    # is about the value reaching the page -- not about how it is escaped.
    goal = "Understand what is causing the breathlessness"

    with_goal = render_report_html(_session(with_report=True, patient_goal=goal))
    without_goal = render_report_html(_session(with_report=True))

    assert goal in with_goal
    assert "Not stated." not in with_goal
    assert "Not stated." in without_goal


def test_special_characters_in_patient_text_do_not_break_rendering() -> None:
    """An unclosed `<` in a patient's own words must not crash ReportLab.

    Paragraph markup is a small XML-like language. Verified directly
    against ReportLab: a bare `&`, a `<digit` (e.g. "<90"), and a
    properly *closed* `<tag>` are all tolerated -- but a `<` immediately
    followed by a letter with no matching `>`/close-tag anywhere later
    in that same paragraph raises `ValueError: paraparser: syntax error`.
    Every string below is a real, confirmed trigger for that failure
    mode, not just an arbitrary `&`/`<` -- this is what would actually
    have caught the missing-escape bug, unlike a milder example.
    """
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    report = PreScreeningReport(
        chief_concern="Chest pain <worsening at night",
        category=SymptomCategory.LUNG,
        clinical_summary="Reports symptoms <worsening over time without improvement",
        findings=[
            Finding(
                question="Is the pain <severe or mild",
                answer="It feels <sharp and constant",
                flagged=True,
            )
        ],
        medications=[Medication(name="Ibuprofen", reason="<chronic joint pain")],
        allergies=[Allergy(allergen="Penicillin", reaction="causes <severe swelling")],
        patient_goal="Wants to know <whether this is serious",
        gaps=["Smoking history <unclear and not confirmed"],
    )
    session = Session(
        session_id="sess_pdf_test",
        appointment_id="appt_pdf_test",
        patient=PatientRef(
            name="Asha <Rao and Co",
            patient_id="pt_2290",
            date_of_birth=datetime(1990, 5, 14, tzinfo=UTC),
            sex=Sex.FEMALE,
        ),
        physician="Dr <Mehta and Associates",
        appointment_datetime=datetime(2026, 9, 3, 9, 30, tzinfo=UTC),
        report=report,
        document_uploaded=True,
        document_summary="This appears to be <an X-ray image",
        created_at=now,
        updated_at=now,
    )

    pdf_bytes = render_report_pdf(session)  # must not raise

    assert pdf_bytes.startswith(b"%PDF-")
    assert pdf_bytes.rstrip().endswith(b"%%EOF")


def test_an_uncategorised_report_still_renders() -> None:
    """A routine checkup belongs to none of the five screened areas.

    The category is the one field on the cover the model may honestly
    leave empty, so rendering must not depend on it being set.
    """
    pdf_bytes = render_report_pdf(_session(with_report=True, category=None))

    assert pdf_bytes.startswith(b"%PDF-")


def test_render_report_pdf_raises_without_a_report() -> None:
    """Rendering a session with no report attached is a programming error, not a soft failure."""
    with pytest.raises(ValueError, match="has no report"):
        render_report_pdf(_session(with_report=False))


def test_appointment_is_rendered_in_local_time_not_raw_utc(non_utc_session: Session) -> None:
    """Regression coverage for a real, shipped bug: a bare `strftime` on
    the UTC-aware `appointment_datetime` used to tell the physician the
    wrong hour whenever the clinic isn't in UTC. 10:00 IST is stored as
    04:30 UTC -- the PDF must say 10:00, not 04:30, and must label the zone."""
    line = _format_appointment(non_utc_session)

    assert "10:00" in line
    assert "04:30" not in line
    assert "IST" in line


def test_appointment_for_a_utc_session_is_unaffected(sample_session: Session) -> None:
    """The existing UTC+0 fixture must keep rendering exactly as before."""
    line = _format_appointment(sample_session)

    assert "09:30" in line


def test_age_years_counts_a_full_elapsed_year() -> None:
    """A birthday that has already passed this year counts the year."""
    assert _age_years(date(1990, 5, 14), date(2026, 9, 1)) == 36


def test_age_years_does_not_count_a_birthday_not_yet_reached() -> None:
    """A birthday later in the year must not be counted early -- a plain
    `as_of.year - dob.year` would overstate the age by one here."""
    assert _age_years(date(1990, 9, 30), date(2026, 9, 1)) == 35


def test_document_link_present_produces_more_content_than_absent() -> None:
    """The optional "Supporting Document" line actually adds to the document.

    Same byte-length proxy as the findings-section test above, for the
    same reason: page count is content-dependent with no forced breaks.
    """
    with_link = render_report_pdf(
        _session(with_report=True),
        "https://example-bucket.s3.amazonaws.com/documents/sess_pdf_test/file?X-Amz-Signature=abc&X-Amz-Expires=1209600",
    )
    without_link = render_report_pdf(_session(with_report=True))

    assert len(with_link) > len(without_link)


def test_attached_documents_section_present_produces_more_content_than_absent() -> None:
    """The ATTACHED DOCUMENTS section actually adds to the document, same byte-length proxy."""
    with_document = render_report_pdf(
        _session(
            with_report=True,
            document_uploaded=True,
            document_summary="This appears to be an X-ray image.",
        )
    )
    without_document = render_report_pdf(_session(with_report=True))

    assert len(with_document) > len(without_document)


def test_attached_documents_section_falls_back_when_summary_missing() -> None:
    """An uploaded document with no summary yet (description generation
    failed, or hasn't run) must still render -- the fallback line -- not
    crash or silently omit the section."""
    pdf_bytes = render_report_pdf(
        _session(with_report=True, document_uploaded=True, document_summary=None)
    )

    assert pdf_bytes.startswith(b"%PDF-")


# --------------------------------------------------------------------------
# Layout: icons on every heading and field, and boxes that line up
# --------------------------------------------------------------------------


def _report_html() -> str:
    return render_report_html(
        _session(with_report=True, medications=[Medication(name="Testomol")]),
        document_download_url="https://example.invalid/d",
    )


def test_every_section_heading_carries_an_icon() -> None:
    """Asserted on the HTML, because a PDF byte stream cannot be."""
    html = _report_html()
    headings = re.findall(r'<div class="sec-hdr[^"]*">(.*?)</div>', html, re.S)

    assert len(headings) >= 7
    for heading in headings:
        assert "<svg" in heading, f"section heading has no icon: {heading[:80]}"


def test_the_two_banners_carry_no_icon() -> None:
    """The banners are a title and a tag, nothing else.

    The section headers below them keep their icons -- there are seven of
    those and the icon is what makes them scannable. The two blue banners
    are already unmissable, so a mark beside them was decoration.
    """
    html = _report_html()

    assert 'class="mark"' not in html
    banners = re.findall(r'<span class="banner-title">(.*?)</span>', html, re.S)
    assert banners == ["Pre-Consultation Report", "Patient Question &amp; Answer Section"]
    assert all("<svg" not in banner for banner in banners)


def test_the_report_is_named_consistently_wherever_it_is_named() -> None:
    """The banner and the document title are the same report."""
    html = _report_html()

    assert "Pre-Consultation Report" in html
    assert "Pre-Screening Report" not in html
    assert "AI Pre-Screening" not in html


def test_every_field_label_on_the_summary_page_carries_an_icon() -> None:
    """The demographics and goals blocks -- not the Q&A, which has no fields."""
    html = _report_html()
    labels = re.findall(r'<span class="field-label">(.*?)</span>', html, re.S)

    assert len(labels) >= 8
    for label in labels:
        assert "<svg" in label, f"field label has no icon: {label[:80]}"


def test_no_value_box_sizes_itself() -> None:
    """The zigzag, pinned.

    Every box used to carry its own inline `flex` weight, and the Q&A
    answers shrink-wrapped their own text, so no two rows began or ended in
    the same place. Widths now come from the grid columns alone.
    """
    html = _report_html()

    assert 'style="flex:' not in html
    assert "width: auto" not in html
    assert re.search(r"\.qa-item\s*\{[^}]*display:\s*grid", html), (
        "Q&A rows are no longer a fixed grid, so the answer boxes can diverge again"
    )
    assert re.search(r"\.value-box\s*\{[^}]*min-height", html), (
        "value boxes have no shared minimum height"
    )


def test_a_patient_with_no_phone_still_gets_a_full_row() -> None:
    """A conditional cell left a dangling half-row and broke the columns below it."""
    session = _session(with_report=True)
    session.patient.contact_phone = None

    html = render_report_html(session)

    assert "Not given." in html
    # Four label/value pairs per block, whether or not each has a value.
    grid = re.search(r'<div class="form-grid">(.*?)</div>\s*</div>', html, re.S)
    assert grid is not None
    assert grid.group(1).count('class="field-label"') == 8


# --------------------------------------------------------------------------
# What the attached document actually is
# --------------------------------------------------------------------------


def _session_with_document(
    *, recent_tests: str | None, document_summary: str | None, uploaded: bool = True
) -> Session:
    session = _session(with_report=True, document_uploaded=uploaded)
    session.document_summary = document_summary
    assert session.report is not None
    session.report.recent_tests = recent_tests
    return session


def test_the_attachment_is_named_by_the_patient_when_they_said_what_it_was() -> None:
    """The reported gap: a CBC report shown as "type could not be determined".

    `describe_document` is bound to the document's modality alone -- it is
    forbidden from naming anything inside the file -- so at its very best it
    reaches "a lab report" where the patient has already said "CBC report".
    It also fails outright more often than it should: an iPhone photo
    uploads as `image/heic`, which the browser accepts and Bedrock cannot
    read, and every one of those lands on the fallback string.

    The patient was asked what the report was, so their answer is what the
    row should carry.
    """
    html = render_report_html(
        _session_with_document(recent_tests="CBC report", document_summary=DESCRIPTION_UNAVAILABLE)
    )

    assert "CBC report" in html
    assert "as the patient described it" in html
    assert DESCRIPTION_UNAVAILABLE not in html


def test_the_automatic_description_is_used_only_when_the_patient_gave_none() -> None:
    """And it says it was automatic, rather than passing as their words."""
    html = render_report_html(
        _session_with_document(
            recent_tests=None, document_summary="This appears to be a lab report."
        )
    )

    assert "This appears to be a lab report." in html
    assert "automatically identified" in html
    assert "as the patient described it" not in html


def test_an_attachment_nobody_could_describe_says_so_plainly() -> None:
    """Neither source has anything, and the row must not invent one."""
    html = render_report_html(
        _session_with_document(recent_tests=None, document_summary=DESCRIPTION_UNAVAILABLE)
    )

    assert "Uploaded; not described." in html
    assert "as the patient described it" not in html
    assert "automatically identified" not in html


def test_no_attachment_is_still_reported_as_none_uploaded() -> None:
    """A patient who sent nothing must not read as one whose file failed."""
    html = render_report_html(
        _session_with_document(recent_tests="CBC report", document_summary=None, uploaded=False)
    )

    assert "None uploaded." in html
    assert "as the patient described it" not in html


# --------------------------------------------------------------------------
# Pagination: no page that exists only because of a page break
# --------------------------------------------------------------------------


def _page_texts(session: Session) -> list[str]:
    reader = PdfReader(BytesIO(render_report_pdf(session)))
    return [(page.extract_text() or "").strip() for page in reader.pages]


def _long_session(finding_count: int) -> Session:
    session = _session(with_report=True)
    assert session.report is not None
    session.report.findings = [
        Finding(
            question=f"Question number {i} about how this has been going?",
            answer=f"Answer number {i}, in the patient's own words",
        )
        for i in range(1, finding_count + 1)
    ]
    return session


@pytest.mark.parametrize("finding_count", [0, 6, 25, 50])
def test_no_page_exists_only_to_hold_a_line_or_two(finding_count: int) -> None:
    """The reported bug: two near-blank pages in the middle of a report.

    A forced `page-break-before` on the Q&A banner turned whatever was left
    of the summary into a page of its own. One real report put `Information
    Gaps` alone on page 2 -- it had overflowed page 1 by three pixels -- and
    the Q&A banner alone on page 3, because the record after it was one
    unbreakable block that could not fit beside it.

    Parameterized across lengths because the original only reproduced at
    one: a fix tuned to a single page count is not a fix.
    """
    pages = _page_texts(_long_session(finding_count))

    for number, text in enumerate(pages, 1):
        assert len(text) > 200, (
            f"page {number} of {len(pages)} holds only {len(text)} characters: {text!r}"
        )


def test_the_qa_banner_is_never_the_last_thing_on_a_page() -> None:
    """A banner is a heading. Stranded at the foot of a page it heads nothing."""
    pages = _page_texts(_long_session(50))

    banner_pages = [i for i, text in enumerate(pages) if "QUESTION & ANSWER SECTION" in text]
    assert len(banner_pages) == 1
    assert "Q1." in pages[banner_pages[0]], "the banner was separated from its first question"


def test_nothing_in_the_template_forces_a_page_break() -> None:
    """The mechanism that manufactured the blank pages, pinned out.

    Content flows and Chromium paginates where it actually runs out of
    room. Keeping a section's header with its body (`page-break-inside`)
    and a banner with its content (`page-break-after`) are both fine --
    deciding in advance that something starts a new page is not.
    """
    html = _report_html()

    assert "page-break-before" not in html
    assert "break-before" not in html
