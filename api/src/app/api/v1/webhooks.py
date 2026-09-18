"""Inbound webhook from the scheduling platform: booking confirmed -> session created."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, status

from app.api.deps import get_booking_platform_client, get_session_service
from app.core.exceptions import BookingWebhookVerificationError
from app.integrations.booking_platform import BookingPlatformClient
from app.schemas.booking import BookingConfirmedWebhook
from app.schemas.session import SessionStatusResponse
from app.services.session_service import SessionService

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post(
    "/booking-confirmed",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SessionStatusResponse,
)
async def booking_confirmed(
    request: Request,
    booking_platform: Annotated[BookingPlatformClient, Depends(get_booking_platform_client)],
    session_service: Annotated[SessionService, Depends(get_session_service)],
    x_signature: Annotated[str | None, Header()] = None,
) -> SessionStatusResponse:
    """Receive a booking-confirmed event and create the pre-screening session.

    Verifies `x_signature` against the raw request body before trusting
    the payload -- a missing or mismatched signature raises
    `BookingWebhookVerificationError`, translated to a 401 by the app's
    exception handler. The signature must be checked against the raw
    bytes, not the parsed model, so the body is read manually here rather
    than declared as a Pydantic request parameter.

    Idempotent by `appointment_id`: see `SessionService.create_from_booking`.
    """
    raw_body = await request.body()
    if x_signature is None or not booking_platform.verify_webhook_signature(raw_body, x_signature):
        raise BookingWebhookVerificationError

    payload = BookingConfirmedWebhook.model_validate_json(raw_body)
    session = await session_service.create_from_booking(payload)

    return SessionStatusResponse.from_session(session)
