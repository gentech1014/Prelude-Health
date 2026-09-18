"""Unit tests for the document-type description helper.

`describe_document`'s actual Bedrock call has no local mock here -- same
as `summarize_session`, this codebase verifies real multimodal model
calls live rather than against a mocked SDK (see the scratchpad live
verification script). These tests cover the deterministic parts instead:
mapping a content-type to the right Bedrock content block, and the
short-circuit that skips the model call entirely for an unsupported
format.
"""

import pytest

from app.agents.summarizer.agent import (
    DESCRIPTION_UNAVAILABLE,
    _build_media_block,
    describe_document,
)
from app.core.config import Settings


@pytest.mark.parametrize(
    ("content_type", "expected_format"),
    [
        ("image/png", "png"),
        ("image/jpeg", "jpeg"),
        ("image/jpg", "jpeg"),
        ("image/gif", "gif"),
        ("image/webp", "webp"),
    ],
)
def test_build_media_block_maps_supported_image_types(
    content_type: str, expected_format: str
) -> None:
    """Each supported image content-type becomes an image block, carrying
    the actual bytes through untouched."""
    block = _build_media_block(content_type, b"file-bytes")

    assert block == {"image": {"format": expected_format, "source": {"bytes": b"file-bytes"}}}


@pytest.mark.parametrize(
    ("content_type", "expected_format"),
    [
        ("application/pdf", "pdf"),
        ("text/csv", "csv"),
        ("text/plain", "txt"),
        ("text/markdown", "md"),
    ],
)
def test_build_media_block_maps_supported_document_types(
    content_type: str, expected_format: str
) -> None:
    """Each supported document content-type becomes a document block --
    `name` is included even though the SDK's TypedDict marks it optional,
    since Bedrock's actual Converse API requires it (confirmed against
    `BedrockModel._format_request_message_content`)."""
    block = _build_media_block(content_type, b"file-bytes")

    assert block == {
        "document": {
            "format": expected_format,
            "name": "supporting-document",
            "source": {"bytes": b"file-bytes"},
        }
    }


def test_build_media_block_is_case_and_parameter_insensitive() -> None:
    """A content-type with charset params or unusual casing still maps --
    real S3/browser clients commonly send values shaped like
    `IMAGE/JPEG; charset=binary`, not the bare lowercase form."""
    block = _build_media_block("IMAGE/JPEG; charset=binary", b"data")

    assert block == {"image": {"format": "jpeg", "source": {"bytes": b"data"}}}


def test_build_media_block_returns_none_for_an_unsupported_type() -> None:
    """A format outside Bedrock's supported image/document lists (e.g. a
    generic binary blob) must not be forced into a guessed block."""
    assert _build_media_block("application/octet-stream", b"data") is None


async def test_describe_document_short_circuits_for_unsupported_format(
    settings: Settings,
) -> None:
    """An unsupported content-type never reaches Bedrock -- it resolves
    straight to the fallback string, with no model call attempted."""
    result = await describe_document("application/octet-stream", b"data", settings)

    assert result == DESCRIPTION_UNAVAILABLE
