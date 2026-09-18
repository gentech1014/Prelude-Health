"""Unit tests for the live-call system prompt's appointment-time rendering.

Regression coverage for a real, shipped bug: the prompt used to
`strftime` the raw UTC-aware `appointment_datetime` with no timezone
conversion, so a patient whose appointment was 10:00 in a non-UTC clinic
was told "10:00" was actually still hours away, or heard the wrong hour
outright. Every other fixture in this suite happens to sit at UTC+0,
which is exactly why no test caught it -- `non_utc_session` (10:00 IST)
exists specifically to catch it.
"""

from app.agents.bidi.prompts import build_intake_prompt
from app.models.session import Session


def test_appointment_time_is_rendered_in_local_time_not_raw_utc(non_utc_session: Session) -> None:
    """10:00 IST is stored as 04:30 UTC -- the prompt must say 10, not 4."""
    prompt = build_intake_prompt(non_utc_session)

    assert "at 10 in the morning" in prompt
    assert "at 4" not in prompt


def test_appointment_context_names_the_correct_day(non_utc_session: Session) -> None:
    """The date itself, not just the hour, must reflect the local
    calendar day -- a late-UTC/early-local (or reverse) instant can fall
    on a different date once converted."""
    prompt = build_intake_prompt(non_utc_session)

    assert "Tuesday 08 September" in prompt


def test_appointment_time_for_a_utc_session_is_unaffected(sample_session: Session) -> None:
    """The existing UTC+0 fixture must keep rendering exactly as before --
    this fix must not change behavior for a clinic actually in UTC."""
    prompt = build_intake_prompt(sample_session)

    assert "at 9:30 in the morning" in prompt
