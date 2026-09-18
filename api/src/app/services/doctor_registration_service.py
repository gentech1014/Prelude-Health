"""Runs the two halves of doctor self-registration.

Registration is a browser redirect round-trip, so it cannot be one call:

* `start` takes the details the doctor typed, parks them under an
  unguessable `state`, and returns the Google consent URL to send them to.
* `complete` is the callback side -- it claims that `state`, trades the
  authorization code for a refresh token, and writes the doctor record with
  their grant attached.

Splitting it this way keeps the route handlers free of flow logic and puts
the security-relevant part (single-use state, no token in any response) in
one place.
"""

import secrets
from datetime import UTC, datetime

import structlog

from app.core.exceptions import DoctorRegistrationStateInvalidError
from app.integrations.google_oauth import GoogleOAuthClient
from app.models.doctor import Doctor, DoctorGoogleGrant, slugify_doctor_name
from app.repositories.doctor_registration_repository import (
    DoctorRegistrationRepository,
    PendingDoctorRegistration,
)
from app.repositories.doctor_repository import DoctorRepository

log = structlog.get_logger(__name__)


class DoctorRegistrationService:
    """Coordinates the pending-registration store, Google OAuth, and the doctor record."""

    def __init__(
        self,
        registrations: DoctorRegistrationRepository,
        doctors: DoctorRepository,
        oauth: GoogleOAuthClient,
        ttl_seconds: int,
    ) -> None:
        self._registrations = registrations
        self._doctors = doctors
        self._oauth = oauth
        self._ttl_seconds = ttl_seconds

    async def start(self, registration: PendingDoctorRegistration) -> str:
        """Park the registration and return the Google consent URL to redirect to.

        Raises `GoogleOAuthNotConfiguredError` before writing anything, so a
        deployment without an OAuth client does not accumulate pending rows
        for a flow that can never complete.
        """
        state = secrets.token_urlsafe(32)
        url = self._oauth.authorization_url(state)

        await self._registrations.create(state, registration, self._ttl_seconds)
        log.info("doctor_registration_started", doctor_name=registration.name)
        return url

    async def complete(self, state: str, code: str) -> Doctor:
        """Claim `state`, exchange `code`, and persist the doctor with their grant.

        Raises `DoctorRegistrationStateInvalidError` when the state is
        unknown, expired, or already spent -- which is also what a replayed
        callback URL looks like.
        """
        pending = await self._registrations.claim(state)
        if pending is None:
            raise DoctorRegistrationStateInvalidError()

        grant = await self._oauth.exchange_code(code)

        # A doctor re-connecting the same Google account updates their own
        # record rather than creating a second one under a renamed slug.
        existing = await self._doctors.get_by_google_email(grant.email)
        doctor_id = existing.doctor_id if existing else slugify_doctor_name(pending.name)

        doctor = Doctor(
            name=pending.name,
            doctor_id=doctor_id,
            google_calendar_id=grant.email,
            credential=pending.credential,
            categories=pending.categories,
            modality=pending.modality,
            # Google's own account photo, so a registering doctor gets a real
            # portrait with nothing to upload. Keeps any existing one when
            # the account has none.
            photo_url=grant.picture_url or (existing.photo_url if existing else None),
            google_grant=DoctorGoogleGrant(
                email=grant.email,
                refresh_token=grant.refresh_token,
                scopes=grant.scopes,
                granted_at=datetime.now(UTC),
            ),
        )
        await self._doctors.register(doctor)
        log.info("doctor_registration_completed", doctor_id=doctor.doctor_id)
        return doctor
