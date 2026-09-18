"""Patient consent, captured before the live intake conversation may begin.

A session must never reach the live call without a recorded, affirmative
consent -- see `app.services.session_service.SessionService.record_consent`
and the gate in `app.api.ws.intake`.
"""

from datetime import datetime

from pydantic import BaseModel


class Consent(BaseModel):
    """Whether -- and when -- the patient consented to the AI intake call."""

    given: bool
    recorded_at: datetime
