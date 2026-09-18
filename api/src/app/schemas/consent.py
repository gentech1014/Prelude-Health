"""API schema for recording patient consent."""

from pydantic import BaseModel


class ConsentRequest(BaseModel):
    """Submitted by the patient's intake page before the live call can start."""

    given: bool
