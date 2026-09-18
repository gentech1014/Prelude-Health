"""The one place AWS credentials are resolved.

Every AWS client in this service -- the live voice model, the summarizer,
object storage -- gets its session from here, built explicitly from this
service's own configuration.

**boto3's own credential resolution is deliberately not used.** Left to
itself, boto3 reads the machine's shared credentials file
(`~/.aws/credentials`), its SSO cache, its container role and its instance
profile. On a developer machine that means whatever `aws sso login` last
wrote -- which is how this service ended up presenting an expired `ASIA…`
STS key while a perfectly good `AKIA…` key sat unused in its own `.env`.
The failure that produces is a `403 ExpiredTokenException` from the middle
of a live patient call, with nothing anywhere mentioning credentials.

What *is* still honoured is the environment, because `Settings` reads
`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN` as
ordinary fields -- from `.env` locally, and from real environment
variables in a container. That is the intended path and the one
`app.core.secrets_loader` populates in production. The distinction is
between *this service's configuration*, wherever it is supplied from, and
*the host's own AWS identity*, which is never borrowed.

So a missing credential is a configuration error raised at the point of
use, and a configured one is the only credential used.

`app.core.secrets_loader` is the single exception, and unavoidably so: it
runs before `Settings` exists, because populating the environment
`Settings` reads is the very thing it does. It uses the ambient chain to
reach Secrets Manager, which in production is an instance role.
"""

import boto3

from app.core.config import Settings
from app.core.exceptions import AwsCredentialsNotConfiguredError


def build_boto_session(
    settings: Settings, *, purpose: str, region: str | None = None
) -> boto3.Session:
    """A session carrying this service's configured AWS credentials.

    Args:
        settings: the app configuration to read credentials from.
        purpose: what the session is for, quoted back in the error when
            credentials are missing so an operator knows which call failed.
        region: overrides `aws_region`. Needed because the summarizer's
            model may live in a different region than the live voice model,
            which is pinned to a region hosting Nova Sonic.

    Raises:
        AwsCredentialsNotConfiguredError: if either key is missing. Raised
            rather than returning an ambient-credential session, because a
            session that silently authenticates as someone else is worse
            than one that does not authenticate at all.
    """
    if not settings.aws_access_key_id or not settings.aws_secret_access_key:
        raise AwsCredentialsNotConfiguredError(purpose)

    return boto3.Session(
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        # None, not "": boto3 treats an empty string as a real token and
        # signs with it, which fails as an invalid-token error rather than
        # as the unsigned request it was meant to be.
        aws_session_token=settings.aws_session_token or None,
        region_name=region or settings.aws_region,
    )


def build_storage_boto_session(settings: Settings) -> boto3.Session:
    """A session for object storage, which may live somewhere else entirely.

    Falls back to the main credentials when `storage_access_key` /
    `storage_secret_key` are blank -- the common case, where the bucket is
    in the same account as Bedrock. Separate keys are for a bucket in a
    different account, or a non-AWS S3-compatible provider reached through
    `storage_endpoint_url`.

    The fallback is to the *configured* credentials, never to the ambient
    chain: blank storage keys mean "same as everything else", not "work it
    out from the machine".
    """
    if settings.storage_access_key and settings.storage_secret_key:
        return boto3.Session(
            aws_access_key_id=settings.storage_access_key,
            aws_secret_access_key=settings.storage_secret_key,
            region_name=settings.storage_region or settings.aws_region,
        )

    return build_boto_session(
        settings,
        purpose="object storage",
        region=settings.storage_region or settings.aws_region,
    )
