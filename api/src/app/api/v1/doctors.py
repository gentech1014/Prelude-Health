"""Doctor self-registration, including the Google OAuth round-trip.

A doctor registers themselves here and grants this service access to their
own calendar, replacing the manual "share your calendar with the service
account" step `app.integrations.google_calendar` depends on. Once granted,
`DoctorCalendarClient` reads their real free/busy for the booking screen
and writes appointments as them.

Two routes because OAuth is a redirect flow: one the booking UI calls, and
one Google calls back into. The callback answers with a redirect rather
than JSON -- it is a browser navigation, so the doctor has to land on a
page. The refresh token it obtains is written straight to Mongo and never
appears in a response body or a redirect URL.
"""

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse

from app.api.deps import SettingsDep, get_doctor_registration_service
from app.repositories.doctor_registration_repository import PendingDoctorRegistration
from app.schemas.booking_ui import DoctorRegistrationRequest, DoctorRegistrationStartResponse
from app.services.doctor_registration_service import DoctorRegistrationService

router = APIRouter(prefix="/doctors", tags=["doctors"])


@router.post("/registration", response_model=DoctorRegistrationStartResponse, status_code=201)
async def start_doctor_registration(
    payload: DoctorRegistrationRequest,
    registration_service: Annotated[
        DoctorRegistrationService, Depends(get_doctor_registration_service)
    ],
) -> DoctorRegistrationStartResponse:
    """Begin registration and return the Google consent URL to navigate to.

    Raises 503 (`GoogleOAuthNotConfiguredError`) when this deployment has
    no Google OAuth client configured, so the UI can say the feature is
    unavailable instead of sending the doctor to a broken consent screen.
    """
    authorization_url = await registration_service.start(
        PendingDoctorRegistration(
            name=payload.name,
            credential=payload.credential,
            categories=payload.categories,
            modality=payload.modality,
        )
    )
    return DoctorRegistrationStartResponse(authorization_url=authorization_url)


@router.get("/google/callback", include_in_schema=False)
async def complete_doctor_registration(
    settings: SettingsDep,
    registration_service: Annotated[
        DoctorRegistrationService, Depends(get_doctor_registration_service)
    ],
    state: Annotated[str | None, Query()] = None,
    code: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    """Google's redirect target: finish the grant and hand the doctor back to the UI.

    Failures redirect with a coarse `doctor_registration=failed` marker
    rather than surfacing Google's own error text -- the UI shows a
    recoverable message, and nothing about the flow's internals travels
    through the doctor's browser history.

    A doctor who clicks "Cancel" on the consent screen arrives here with
    `error=access_denied` and no code; that is a declined grant, not a
    fault, and gets its own marker so the UI can stay quiet about it.
    """
    if error or not state or not code:
        outcome = "declined" if error == "access_denied" else "failed"
        return RedirectResponse(_booking_redirect(settings.booking_app_base_url, outcome))

    try:
        doctor = await registration_service.complete(state=state, code=code)
    except Exception:
        # Deliberately broad: every failure mode here (expired state,
        # rejected code, Mongo write) has the same recovery -- start again --
        # and a raised error would render as JSON in the doctor's browser
        # instead of returning them to the UI.
        return RedirectResponse(_booking_redirect(settings.booking_app_base_url, "failed"))

    return RedirectResponse(
        _booking_redirect(settings.booking_app_base_url, "connected", doctor.name)
    )


def _booking_redirect(base_url: str, outcome: str, doctor_name: str | None = None) -> str:
    """Build the post-consent URL back into the booking UI.

    Carries only the outcome and the doctor's own display name -- no token,
    no email, no PHI, since this URL lands in browser history.
    """
    params = {"doctor_registration": outcome}
    if doctor_name:
        params["doctor"] = doctor_name
    return f"{base_url.rstrip('/')}/book?{urlencode(params)}"
