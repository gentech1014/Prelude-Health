"""Unit tests for transcript flattening.

`render_transcript_as_text` is what stands between the stored transcript
and the summarization agent. It exists to strip replayable tool-call
structure, and to preserve turn order so a later correction can override
an earlier answer -- both are pinned here.
"""

from datetime import UTC, datetime

from app.models.transcript import ConversationTurn, render_transcript_as_text


def _turn(role: str, text: str, minute: int) -> ConversationTurn:
    return ConversationTurn(
        role=role,  # type: ignore[arg-type]
        text=text,
        ts=datetime(2026, 9, 1, 9, minute, tzinfo=UTC),
    )


def test_empty_transcript_renders_empty_string() -> None:
    """A session with no turns must not blow up the summarizer."""
    assert render_transcript_as_text([]) == ""


def test_roles_render_as_human_readable_speakers() -> None:
    """The model sees speaker labels, not raw API role names."""
    rendered = render_transcript_as_text(
        [
            _turn("assistant", "Hi Asha, this is a short pre-visit call.", 12),
            _turn("user", "I've been out of breath climbing stairs.", 13),
        ]
    )

    assert rendered == (
        "Assistant: Hi Asha, this is a short pre-visit call.\n"
        "Patient: I've been out of breath climbing stairs."
    )


def test_turn_order_is_preserved_so_corrections_win() -> None:
    """A correction made later in the call must appear after the original.

    The summarizer is instructed that later statements override earlier
    ones, which only holds if the rendering keeps chronological order.
    """
    rendered = render_transcript_as_text(
        [
            _turn("user", "About two weeks.", 20),
            _turn("assistant", "So, two weeks. Is that right?", 30),
            _turn("user", "Actually no, more like two months.", 31),
        ]
    )

    lines = rendered.splitlines()
    assert lines[0] == "Patient: About two weeks."
    assert lines[-1] == "Patient: Actually no, more like two months."


def test_rendering_contains_no_tool_call_structure() -> None:
    """Only speaker-labelled text, so nothing can be replayed as an action."""
    rendered = render_transcript_as_text([_turn("assistant", "Let me pull up some questions.", 14)])

    assert "toolUse" not in rendered
    assert "start_prescreening" not in rendered
    assert rendered.startswith("Assistant: ")
