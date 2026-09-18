"""Construction of the live conversational agent.

Uses `strands.experimental.bidi` -- labeled experimental upstream, so the
exact `strands-agents` version is pinned in `pyproject.toml` rather than
left as a floating minimum.
"""

from strands.experimental.bidi import BidiAgent
from strands.experimental.bidi.models import BidiNovaSonicModel

from app.agents.bidi.call_state import CallProgress
from app.agents.bidi.prompts import build_intake_prompt
from app.agents.bidi.symptom_plan import SymptomQuestionPlan
from app.agents.tools.appointment import move_appointment, offer_appointment_times
from app.agents.tools.intake_details import record_intake_details
from app.agents.tools.navigation import get_current_screen, navigate_to_screen
from app.agents.tools.scheduling import cancel_appointment
from app.agents.tools.session_control import end_session
from app.agents.tools.symptoms import (
    ask_symptom_question,
    record_symptom_answer,
    start_prescreening,
)
from app.agents.tools.upload import request_document_upload
from app.core.aws import build_boto_session
from app.core.config import Settings
from app.models.session import Session


def build_live_agent(
    session: Session,
    settings: Settings,
    progress: CallProgress | None = None,
    symptoms: SymptomQuestionPlan | None = None,
) -> BidiAgent:
    """Construct a fresh `BidiAgent` for one connection of one intake call.

    Must be called once per connection, never reused -- including across a
    reconnect of the same session, which gets a new agent carrying a
    resume block built from `progress` and `symptoms`.

    Per-call context (the `LiveCallContext`, the question-bank service) is
    threaded in separately via `agent.run(invocation_state=...)` when the
    call begins (see `app.api.ws.intake`), not baked in here.
    """
    model = BidiNovaSonicModel(
        model_id=settings.bidi_model_id,
        provider_config={
            "audio": {"voice": settings.bidi_voice},
            "inference": {"temperature": 0.3},  # keep intake literal, not chatty
            # Nova Sonic v2 only -- raises ValueError against v1.
            "turn_detection": {"endpointingSensitivity": settings.bidi_endpointing_sensitivity},
        },
        # An explicit session, not `{"region": ...}`: that form leaves
        # credentials to boto3's ambient chain, which outranks this
        # service's own configuration (see `app.core.aws`). The region
        # travels on the session because Nova 2 Sonic has no cross-region
        # inference and must be called in a region that hosts it --
        # `client_config` rejects `boto_session` and `region` together.
        client_config={
            "boto_session": build_boto_session(settings, purpose="the live voice model")
        },
    )

    return BidiAgent(
        model=model,
        system_prompt=build_intake_prompt(session, progress, symptoms),
        # Navigation first: it is the tool the agent must reach for on
        # every topic change, and tool order is the order it is described
        # to the model.
        tools=[
            navigate_to_screen,
            record_intake_details,
            get_current_screen,
            start_prescreening,
            ask_symptom_question,
            record_symptom_answer,
            request_document_upload,
            end_session,
            cancel_appointment,
            offer_appointment_times,
            move_appointment,
        ],
    )
