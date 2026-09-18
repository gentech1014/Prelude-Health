"""Google OAuth 2.0 authorization-code flow for doctor self-registration.

Lets a doctor connect their *own* Google account and grant this service
read/write access to their calendar, instead of the out-of-band arrangement
`app.integrations.google_calendar` needs (each doctor manually sharing a
calendar with one shared service account).

Implemented directly against Google's OAuth endpoints with the shared
`httpx.AsyncClient` rather than pulling in `google-auth-oauthlib`: the flow
is two POSTs and a URL, all of it async, and the stored refresh token is
consumed by `google.oauth2.credentials.Credentials`, which `google-auth`
already provides.
"""

from urllib.parse import urlencode

import httpx
import structlog

from app.core.config import Settings
from app.core.exceptions import GoogleOAuthExchangeError, GoogleOAuthNotConfiguredError

log = structlog.get_logger(__name__)

_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"

CALENDAR_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    # Only for the name/photo shown on the doctor's own booking card.
    "https://www.googleapis.com/auth/userinfo.profile",
    # Full calendar scope, not `calendar.events`: the doctor is granting
    # read, create, update and delete on their appointment calendar, which
    # is what tracking their schedule from here requires.
    "https://www.googleapis.com/auth/calendar",
]


class GoogleOAuthGrant:
    """One completed authorization: who granted it, and the token to reuse it."""

    def __init__(
        self,
        email: str,
        refresh_token: str,
        scopes: list[str],
        picture_url: str | None = None,
    ) -> None:
        self.email = email
        self.refresh_token = refresh_token
        self.scopes = scopes
        self.picture_url = picture_url
        """The account's own profile photo, when Google returned one. Used as
        the doctor's booking-card portrait so registering needs no upload."""


class GoogleOAuthClient:
    """Builds consent URLs and exchanges authorization codes for tokens."""

    def __init__(self, settings: Settings, http_client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http_client

    @property
    def is_configured(self) -> bool:
        """Whether this deployment has an OAuth client to run the flow with."""
        return bool(
            self._settings.google_oauth_client_id and self._settings.google_oauth_client_secret
        )

    def authorization_url(self, state: str) -> str:
        """Consent URL to send the doctor's browser to.

        `access_type=offline` with `prompt=consent` is what makes Google
        return a refresh token: without both, a doctor who has authorized
        before gets an access token only, and calendar access silently
        stops working an hour later.
        """
        if not self.is_configured:
            raise GoogleOAuthNotConfiguredError()

        query = urlencode(
            {
                "client_id": self._settings.google_oauth_client_id,
                "redirect_uri": self._settings.google_oauth_redirect_uri,
                "response_type": "code",
                "scope": " ".join(CALENDAR_SCOPES),
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true",
                "state": state,
            }
        )
        return f"{_AUTH_ENDPOINT}?{query}"

    async def exchange_code(self, code: str) -> GoogleOAuthGrant:
        """Trade the callback's `code` for a refresh token and the grantor's email."""
        if not self.is_configured:
            raise GoogleOAuthNotConfiguredError()

        token_response = await self._http.post(
            _TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": self._settings.google_oauth_client_id,
                "client_secret": self._settings.google_oauth_client_secret,
                "redirect_uri": self._settings.google_oauth_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if token_response.status_code != httpx.codes.OK:
            # Google's body echoes the request; log the status only.
            log.error("google_oauth_token_exchange_rejected", status=token_response.status_code)
            raise GoogleOAuthExchangeError("Google rejected the authorization code")

        payload = token_response.json()
        refresh_token = payload.get("refresh_token")
        access_token = payload.get("access_token")
        if not refresh_token or not access_token:
            raise GoogleOAuthExchangeError("Google returned no refresh token")

        email, picture_url = await self._fetch_profile(access_token)
        granted_scopes = str(payload.get("scope", "")).split()
        return GoogleOAuthGrant(
            email=email,
            refresh_token=refresh_token,
            scopes=granted_scopes,
            picture_url=picture_url,
        )

    async def _fetch_profile(self, access_token: str) -> tuple[str, str | None]:
        """Read the granting account's email and photo from the userinfo endpoint.

        The email is required -- it is the doctor's calendar id. The photo is
        not: an account without one simply has no portrait, and the booking
        card falls back to initials.
        """
        response = await self._http.get(
            _USERINFO_ENDPOINT, headers={"Authorization": f"Bearer {access_token}"}
        )
        if response.status_code != httpx.codes.OK:
            raise GoogleOAuthExchangeError("Could not read the Google account email")

        payload = response.json()
        email = payload.get("email")
        if not email:
            raise GoogleOAuthExchangeError("Google account has no email address")

        picture = payload.get("picture")
        return str(email), str(picture) if picture else None
