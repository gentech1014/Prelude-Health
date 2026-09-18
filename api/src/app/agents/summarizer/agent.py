"""Post-call summarization agent.

Non-live, and stateless per call: a `strands.Agent` must be constructed
fresh for every invocation, never shared across concurrent sessions -- a
second concurrent invocation on the same instance raises
`ConcurrencyException`.
"""

import structlog
from pydantic import BaseModel
from strands import Agent
from strands.models import BedrockModel
from strands.types.content import ContentBlock
from strands.types.media import DocumentFormat, ImageFormat

from app.agents.summarizer.prompts import build_document_description_prompt, build_summary_prompt
from app.core.aws import build_boto_session
from app.core.config import Settings
from app.core.exceptions import SummarizationFailedError
from app.models.report import PreScreeningReport
from app.models.symptom_intake import SymptomAnswer
from app.models.transcript import ConversationTurn, render_transcript_as_text

log = structlog.get_logger(__name__)


async def summarize_session(
    session_id: str,
    turns: list[ConversationTurn],
    settings: Settings,
    recorded_details: dict[str, dict[str, str]] | None = None,
    symptom_answers: list[SymptomAnswer] | None = None,
    recorded_statuses: dict[str, dict[str, str]] | None = None,
) -> PreScreeningReport:
    """Produce a `PreScreeningReport` from a completed session's records.

    `recorded_details` is what the agent confirmed on the patient's screen
    as their answers landed (`Session.collected_details`). It goes in
    alongside the transcript because a live transcription drops words, and
    `recorded_statuses` says how well each of those values is known --
    confirmed, uncertain, inferred, undisclosed or unknown. Without it the
    report could not tell a physician whether a missing answer was one the
    patient declined, one they could not remember, or one nobody asked.

    an answer that survives only there is still an answer the patient
    gave -- the prompt ranks the transcript above it, so a correction the
    patient made later still wins.

    `symptom_answers` is the symptom screen's own question-and-answer
    record (`Session.symptom_answers`). It goes in as its own section
    because it already has the shape of `PreScreeningReport.findings`,
    which the flat recorded values do not.

    Passes the transcript as a flattened string, not raw Strands `Message`
    objects: replaying stored messages as structured history risks the
    agent re-executing a trailing tool-call block (e.g. `end_session`)
    with no model turn in between. See `render_transcript_as_text`.

    Raises `SummarizationFailedError` if the model returns no structured
    output -- a turn/token budget limit can end a call silently with
    `structured_output=None` and no exception, so this must be checked
    explicitly rather than assumed on a non-error return.
    """
    agent = Agent(
        model=BedrockModel(
            model_id=settings.summary_model_id,
            # An explicit session rather than `region_name`, so credentials
            # come from this service's configuration instead of boto3's
            # ambient chain (see `app.core.aws`). `BedrockModel` rejects
            # `boto_session` and `region_name` together, so the region
            # travels on the session -- and it is not always `aws_region`:
            # an application-inference-profile ARN must be invoked from the
            # region it was created in, which can differ from where the
            # live voice model is pinned.
            boto_session=build_boto_session(
                settings,
                purpose="summarization",
                region=settings.summary_model_region or settings.aws_region,
            ),
            temperature=0.2,  # extraction, not composition -- favour faithfulness
        ),
        callback_handler=None,  # suppress console printing in a backend job
    )

    result = await agent.invoke_async(
        build_summary_prompt(
            render_transcript_as_text(turns),
            recorded_details,
            symptom_answers,
            recorded_statuses,
        ),
        structured_output_model=PreScreeningReport,
    )

    # `structured_output` is typed as the base model, and a budget trip can
    # end the call cleanly with it unset and no exception raised -- so both
    # its presence and its type are checked rather than assumed.
    report = result.structured_output
    if not isinstance(report, PreScreeningReport):
        raise SummarizationFailedError(session_id, result.stop_reason)

    return report


class _DocumentDescription(BaseModel):
    """Structured output for one document-type description call.

    Not a domain model and never persisted as-is -- `describe_document`
    extracts `.description` and stores that plain string on
    `Session.document_summary`. Kept local to this module rather than in
    `app.models.report` since nothing else ever needs this shape.
    """

    description: str


_CONTENT_TYPE_TO_IMAGE_FORMAT: dict[str, ImageFormat] = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/gif": "gif",
    "image/webp": "webp",
}
_CONTENT_TYPE_TO_DOCUMENT_FORMAT: dict[str, DocumentFormat] = {
    "application/pdf": "pdf",
    "text/csv": "csv",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "text/html": "html",
    "text/plain": "txt",
    "text/markdown": "md",
}

DESCRIPTION_UNAVAILABLE = "Document type could not be automatically determined."


def _build_media_block(content_type: str, data: bytes) -> ContentBlock | None:
    """Map a stored content-type to the Bedrock content block it needs.

    Returns `None` for anything outside Bedrock's supported image/document
    formats (confirmed against the installed `strands.types.media`) -- the
    caller treats that the same as a failed description, not an error.
    """
    base_type = content_type.split(";", 1)[0].strip().lower()
    if image_format := _CONTENT_TYPE_TO_IMAGE_FORMAT.get(base_type):
        return {"image": {"format": image_format, "source": {"bytes": data}}}
    if document_format := _CONTENT_TYPE_TO_DOCUMENT_FORMAT.get(base_type):
        return {
            "document": {
                "format": document_format,
                "name": "supporting-document",
                "source": {"bytes": data},
            }
        }
    return None


async def describe_document(content_type: str, data: bytes, settings: Settings) -> str:
    """Describe an uploaded supporting document's TYPE only -- never its
    contents. See `DOCUMENT_DESCRIPTION_INSTRUCTIONS` for the exact,
    deliberately narrow rules this call is bound by: modality only, never
    a finding, a diagnosis, or anything actually visible inside the file.

    Never raises. An unrecognized format, a model failure, or an empty
    structured output all resolve to `DESCRIPTION_UNAVAILABLE` -- this is
    a nice-to-have layered on top of the document link, matching the
    best-effort convention `SessionService._append_calendar_link` already
    uses for document delivery, and must never block it.
    """
    media_block = _build_media_block(content_type, data)
    if media_block is None:
        log.info("document_description_unsupported_format", content_type=content_type)
        return DESCRIPTION_UNAVAILABLE

    try:
        agent = Agent(
            model=BedrockModel(
                model_id=settings.summary_model_id,
                region_name=settings.summary_model_region or settings.aws_region,
                temperature=0.0,  # stating a type, not composing -- no room for embellishment
            ),
            callback_handler=None,
        )
        result = await agent.invoke_async(
            build_document_description_prompt(media_block),
            structured_output_model=_DocumentDescription,
        )
    except Exception as exc:
        # Not `log.exception(...)`: a Bedrock validation error's own message
        # can carry non-ASCII characters (confirmed live -- a rejected
        # image came back with Unicode box-drawing characters in the
        # error text), and structlog's console renderer then raises
        # `UnicodeEncodeError` on a Windows terminal's cp1252 stdout,
        # which would defeat this function's whole "never raises"
        # contract. Sanitizing to ASCII before logging keeps that
        # contract regardless of what a provider's error text contains.
        log.error(
            "document_description_errored",
            error=str(exc).encode("ascii", "backslashreplace").decode("ascii"),
        )
        return DESCRIPTION_UNAVAILABLE

    description = result.structured_output
    if not isinstance(description, _DocumentDescription):
        log.warning("document_description_no_structured_output", stop_reason=result.stop_reason)
        return DESCRIPTION_UNAVAILABLE
    return description.description
