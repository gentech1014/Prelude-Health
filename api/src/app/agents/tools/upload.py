"""Tool: opens the frontend's document-upload widget, without touching the file itself.

Documents are handled fully out-of-band, and under Nova Sonic that is not
a workaround but the only option: Nova Sonic is a speech-to-speech model
and does not accept image input on the live connection at all.

So this tool never receives or forwards the file. It only signals intent,
by publishing an `upload_requested` frame on the patient's own channel.
Deliberately *not* by letting the frontend watch raw `tool_use_stream`
events: those arrive as partially-streamed argument deltas, several per
call, and would put this tool's name and internals on the wire. The file
goes straight to object storage and is read back afterwards by the
physician view, keyed by `session_id`.
"""

from strands import tool
from strands.types.tools import ToolContext

from app.agents.bidi.call_state import live_call
from app.schemas import intake_channel


@tool(context=True)
def request_document_upload(tool_context: ToolContext) -> str:
    """Open the upload panel, so the patient can send a document they have.

    Call this the moment the patient mentions having anything written
    down -- a scan, a lab result, a discharge note, a letter from another
    doctor. Not to find out whether one exists: they have already said so,
    and asking again is the second time they have been asked.

    Then ask them to upload it or take a photo of it. Do not wait for the
    file and do not ask whether it finished.

    You do not need this on the `recent-care` screen when they say they
    have had tests -- recording that opens the panel by itself.

    Args:
        tool_context: injected by the agent runtime; carries the
            per-call `invocation_state`.
    """
    context = live_call(tool_context.invocation_state)
    if not context.consent_given:
        return (
            "Consent has not been recorded yet, so you cannot ask for a document. "
            "Ask the patient to give consent on screen first."
        )

    context.upload_prompted = True
    context.bus.publish(intake_channel.upload_requested())
    # The old reply here opened with "Carry on talking", and the model said
    # exactly that back to the patient -- "Okay, carry on" -- in place of
    # asking for the document it had just put a control on screen for. A
    # tool reply that describes an action gets narrated; one that names the
    # next thing to say gets said.
    return (
        "The upload panel is open on their screen. Now say, in your own words and "
        "in one short sentence, that they can upload it or take a photo of it right "
        "there. Then ask your next question. Do not wait for the file, do not ask "
        "whether it finished, do not mention screens or tools, and never say "
        "'carry on'. You will be told when the file arrives, or when it fails -- "
        "that is when you ask what the report is, and not before."
    )
