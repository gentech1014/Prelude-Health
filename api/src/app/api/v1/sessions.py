"""Session attach, status, context, consent, and WebSocket-ticket endpoints.

The patient app's whole authenticated lifecycle lives here. `attach` is the
door: it is the only route that accepts the intake link's token, and it
trades that token for an `HttpOnly` cookie every other route requires.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.api.deps import (
    SettingsDep,
    get_doctor_repository,
    get_session_repository,
    get_session_service,
    get_ws_ticket_repository,
    require_intake_token,
    require_session_cookie,
)
from app.core.config import Settings
from app.core.constants import SessionState
from app.core.exceptions import SessionNotFoundError
from app.core.security import generate_session_token, generate_ws_ticket
from app.models.session import Session
from app.repositories.doctor_repository import DoctorRepository
from app.repositories.session_repository import SessionRepository
from app.repositories.ws_ticket_repository import WsTicketRepository
from app.schemas.consent import ConsentRequest
from app.schemas.session import (
    SessionContextResponse,
    SessionStatusResponse,
    WsTicketResponse,
)
from app.services.session_service import SessionService

router = APIRouter(prefix="/sessions", tags=["sessions"])

# Opening the link is what STARTED means. Re-attaching later (a refresh, a
# second device, a rejoin after a dropped call) must not drag a session
# backwards out of IN_PROGRESS or INTERRUPTED.
_ATTACHABLE_TO_STARTED = frozenset({SessionState.NOTIFICATION_SENT, SessionState.AI_LINK_READY})


async def _context_for(
    session: Session,
    settings: Settings,
    doctors: DoctorRepository,
) -> SessionContextResponse:
    """Assemble the context response, including the doctor's own record.

    The doctor lookup is by free-text name, which is how sessions store
    their physician -- a typo simply yields no record, and the response
    goes out without the credential line rather than failing.
    """
    doctor = await doctors.get_by_name(session.physician)
    return SessionContextResponse.from_session(session, settings, doctor)


def _set_session_cookie(response: Response, session_id: str, settings: Settings) -> None:
    """Issue the `HttpOnly` session cookie for one session.

    `secure` is forced on whenever SameSite=None, because browsers reject
    that combination outright and the resulting failure -- a cookie that
    is simply never stored -- is invisible from the server side.
    """
    same_site = settings.session_cookie_samesite
    response.set_cookie(
        key=settings.session_cookie_name,
        value=generate_session_token(
            session_id, settings.intake_link_secret, settings.session_cookie_ttl_seconds
        ),
        max_age=settings.session_cookie_ttl_seconds,
        httponly=True,
        secure=settings.session_cookie_secure or same_site == "none",
        samesite=same_site,
        path="/",
    )


@router.post(
    "/{session_id}/attach",
    response_model=SessionContextResponse,
    dependencies=[Depends(require_intake_token)],
)
async def attach_session(
    session_id: str,
    response: Response,
    settings: SettingsDep,
    session_repository: Annotated[SessionRepository, Depends(get_session_repository)],
    session_service: Annotated[SessionService, Depends(get_session_service)],
    doctor_repository: Annotated[DoctorRepository, Depends(get_doctor_repository)],
) -> SessionContextResponse:
    """Exchange the intake link's token for a session cookie, and return context.

    The one route that accepts `?token=`. The patient app calls it once,
    on load, then strips the token from the URL -- from that point the
    `HttpOnly` cookie authenticates every other call, and the token is
    never in the address bar, browser history, or a `Referer` header
    again. Page JavaScript cannot read the cookie, so an XSS on the
    patient app cannot lift the credential either.

    Also transitions the session to STARTED, which is what "the patient
    opened their link" has always meant -- until now nothing called
    `start_call`, so that state was defined but never written.
    """
    session = await session_repository.get_by_id(session_id)
    if session is None:
        raise SessionNotFoundError(session_id)

    if session.status in _ATTACHABLE_TO_STARTED:
        session = await session_service.start_call(session_id)

    _set_session_cookie(response, session_id, settings)
    return await _context_for(session, settings, doctor_repository)


@router.get(
    "/{session_id}",
    response_model=SessionStatusResponse,
    dependencies=[Depends(require_session_cookie)],
)
async def get_session_status(
    session_id: str,
    session_repository: Annotated[SessionRepository, Depends(get_session_repository)],
) -> SessionStatusResponse:
    """Return the current status of a session.

    The pollable route: safe to call repeatedly while a summary is being
    generated, and deliberately carries no PHI. Raises 404 if `session_id`
    does not exist, 401 if the session cookie is missing or invalid.
    """
    session = await session_repository.get_by_id(session_id)
    if session is None:
        raise SessionNotFoundError(session_id)

    return SessionStatusResponse.from_session(session)


@router.get(
    "/{session_id}/context",
    response_model=SessionContextResponse,
    dependencies=[Depends(require_session_cookie)],
)
async def get_session_context(
    session_id: str,
    settings: SettingsDep,
    session_repository: Annotated[SessionRepository, Depends(get_session_repository)],
    doctor_repository: Annotated[DoctorRepository, Depends(get_doctor_repository)],
) -> SessionContextResponse:
    """Return the patient and appointment detail behind this session.

    Separate from the status route because this one carries PHI: the
    patient app needs it to greet a real person and let them confirm real
    details, but nothing should be polling it.

    Also what a mid-call refresh reads: it carries the resume marker and
    everything collected so far, so a reload lands the patient back on the
    screen the call had reached rather than at the beginning.
    """
    session = await session_repository.get_by_id(session_id)
    if session is None:
        raise SessionNotFoundError(session_id)

    return await _context_for(session, settings, doctor_repository)


@router.post(
    "/{session_id}/consent",
    response_model=SessionContextResponse,
    dependencies=[Depends(require_session_cookie)],
)
async def submit_consent(
    session_id: str,
    payload: ConsentRequest,
    settings: SettingsDep,
    session_service: Annotated[SessionService, Depends(get_session_service)],
    doctor_repository: Annotated[DoctorRepository, Depends(get_doctor_repository)],
) -> SessionContextResponse:
    """Record the patient's consent decision before the live call can start.

    Must be called -- and must return `consent_given=True` -- before the
    WebSocket intake route will proceed past its consent gate; the live
    agent must never start collecting information without recorded
    consent. A `given=False` decision moves the session to DECLINED.

    Returns the full context rather than bare status so the patient app
    can render the next screen from one response.
    """
    session = await session_service.record_consent(session_id, payload.given)
    return await _context_for(session, settings, doctor_repository)


@router.post(
    "/{session_id}/ws-ticket",
    response_model=WsTicketResponse,
    dependencies=[Depends(require_session_cookie)],
)
async def create_ws_ticket(
    session_id: str,
    settings: SettingsDep,
    ws_tickets: Annotated[WsTicketRepository, Depends(get_ws_ticket_repository)],
) -> WsTicketResponse:
    """Mint a single-use, seconds-long ticket for one WebSocket connection.

    A browser cannot set headers on a WebSocket handshake, so the
    credential has to travel in the URL -- which is exactly where a
    long-lived token should never be. A ticket squares that: it is minted
    by an already-authenticated session, lives for `ws_ticket_ttl_seconds`,
    and is spent on first connect (see `WsTicketRepository`), so lifting
    it from a log buys nothing.

    Nothing is recorded here. The nonce is claimed at connect time, not at
    mint time, so a ticket the patient never uses costs no write.
    """
    del ws_tickets  # claimed at connect time, in app.api.ws.intake
    return WsTicketResponse(
        ticket=generate_ws_ticket(
            session_id, settings.intake_link_secret, settings.ws_ticket_ttl_seconds
        ),
        expires_in_seconds=settings.ws_ticket_ttl_seconds,
    )
