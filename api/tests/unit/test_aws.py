"""Where AWS credentials come from.

These pin a failure that was invisible from the outside and cost a live
call. `Settings` did not declare `aws_access_key_id`/`aws_secret_access_key`,
so a value set in `.env` was silently dropped by `extra="ignore"` and boto3
resolved the machine's own shared-credentials file instead. The observable
symptom was a `403 ExpiredTokenException` from the middle of a patient's
voice call -- nothing anywhere said "credentials".

The rule these tests defend: the configured credential is the only
credential used, and a missing one is a configuration error rather than a
silent fall-through to whoever the machine happens to be.
"""

from pathlib import Path

import boto3
import pytest

from app.core.aws import build_boto_session, build_storage_boto_session
from app.core.config import Settings
from app.core.exceptions import AwsCredentialsNotConfiguredError


def _settings(**overrides: object) -> Settings:
    return Settings(
        _env_file=None,  # isolate from the developer's real .env -- see tests/conftest.py
        intake_link_secret="unused-but-required-secret-32-chars",  # noqa: S106 - test fixture
        booking_webhook_secret="unused-but-required-secret-32-chars",  # noqa: S106 - test fixture
        physician_api_key="unused-but-required-key-32-characters",  # noqa: S106 - test fixture
        **overrides,  # type: ignore[arg-type]
    )


def test_settings_actually_reads_aws_credentials() -> None:
    """The regression itself: an undeclared field is dropped, not surfaced.

    `Settings` uses `extra="ignore"`, so before these fields existed an
    operator could set them correctly and have them go nowhere.
    """
    settings = _settings(aws_access_key_id="AKIAEXAMPLE", aws_secret_access_key="secret")

    assert settings.aws_access_key_id == "AKIAEXAMPLE"
    assert settings.aws_secret_access_key == "secret"  # noqa: S105 - test fixture


@pytest.fixture
def machine_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    """A shared credentials file, as `aws sso login` would leave behind.

    This is the path that actually hijacked the service: boto3 resolved an
    expired `ASIA…` key from here (`method=shared-credentials-file`) in
    preference to the `AKIA…` key configured in `.env`. Environment
    variables are cleared too, since `Settings` legitimately reads those.
    """
    profile = tmp_path / "credentials"
    profile.write_text(
        "[default]\n"
        "aws_access_key_id = ASIA_FROM_THE_MACHINE\n"
        "aws_secret_access_key = expired-machine-secret\n"
        "aws_session_token = expired-machine-token\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(profile))
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    return "ASIA_FROM_THE_MACHINE"


def test_the_configured_credential_beats_the_machines_own(machine_credentials: str) -> None:
    """The regression, reproduced: our key must win over the shared file."""
    session = build_boto_session(
        _settings(aws_access_key_id="AKIA_FROM_OUR_ENV", aws_secret_access_key="ours"),
        purpose="a test",
    )

    credentials = session.get_credentials()
    assert credentials is not None
    assert credentials.get_frozen_credentials().access_key == "AKIA_FROM_OUR_ENV"
    assert credentials.get_frozen_credentials().access_key != machine_credentials


def test_a_bare_session_would_have_used_the_machines_credentials(
    machine_credentials: str,
) -> None:
    """Proves the fixture is a real hazard, not a hypothetical one.

    Without this, the test above could pass simply because the machine had
    no credentials to find.
    """
    ambient = boto3.Session().get_credentials()

    assert ambient is not None
    assert ambient.get_frozen_credentials().access_key == machine_credentials


def test_missing_credentials_raise_rather_than_falling_back(
    machine_credentials: str,
) -> None:
    """A session authenticating as someone else is worse than none at all."""
    del machine_credentials  # the hazard is the point; its value is not

    with pytest.raises(AwsCredentialsNotConfiguredError) as raised:
        build_boto_session(_settings(), purpose="the live voice model")

    # The message has to name what failed and what to set -- an operator
    # reading a 503 has nothing else to go on.
    assert "the live voice model" in str(raised.value)
    assert "AWS_ACCESS_KEY_ID" in str(raised.value)
    assert raised.value.status_code == 503


def test_a_half_configured_credential_is_still_a_failure() -> None:
    """One key without the other cannot sign anything."""
    with pytest.raises(AwsCredentialsNotConfiguredError):
        build_boto_session(_settings(aws_access_key_id="AKIAEXAMPLE"), purpose="a test")

    with pytest.raises(AwsCredentialsNotConfiguredError):
        build_boto_session(_settings(aws_secret_access_key="secret"), purpose="a test")


def test_a_blank_session_token_is_not_sent_as_a_token() -> None:
    """A permanent IAM key has no token, and `""` is not the same as absent.

    boto3 signs with an empty string if given one, which fails as an
    invalid-token error rather than as the unsigned-token request a
    permanent key needs.
    """
    session = build_boto_session(
        _settings(aws_access_key_id="AKIAEXAMPLE", aws_secret_access_key="secret"),
        purpose="a test",
    )

    credentials = session.get_credentials()
    assert credentials is not None
    assert credentials.get_frozen_credentials().token is None


def test_a_session_token_is_passed_through_when_configured() -> None:
    """Temporary credentials (an `ASIA…` key) are rejected without it."""
    session = build_boto_session(
        _settings(
            aws_access_key_id="ASIAEXAMPLE",
            aws_secret_access_key="secret",
            aws_session_token="temporary-token",  # noqa: S106 - test fixture
        ),
        purpose="a test",
    )

    credentials = session.get_credentials()
    assert credentials is not None
    assert credentials.get_frozen_credentials().token == "temporary-token"  # noqa: S105


def test_region_can_be_overridden_for_a_differently_located_model() -> None:
    """The summarizer's inference profile may not live in Nova Sonic's region."""
    settings = _settings(
        aws_region="ap-northeast-1",
        aws_access_key_id="AKIAEXAMPLE",
        aws_secret_access_key="secret",
    )

    assert build_boto_session(settings, purpose="a test").region_name == "ap-northeast-1"
    assert (
        build_boto_session(settings, purpose="a test", region="us-east-1").region_name
        == "us-east-1"
    )


def test_storage_falls_back_to_the_main_credentials_not_to_the_machine(
    machine_credentials: str,
) -> None:
    """Blank storage keys mean "same account", never "work it out"."""
    del machine_credentials

    session = build_storage_boto_session(
        _settings(aws_access_key_id="AKIA_FROM_OUR_ENV", aws_secret_access_key="ours")
    )

    credentials = session.get_credentials()
    assert credentials is not None
    assert credentials.get_frozen_credentials().access_key == "AKIA_FROM_OUR_ENV"


def test_storage_uses_its_own_credentials_when_it_has_them() -> None:
    """A bucket in another account, or a non-AWS S3-compatible provider."""
    session = build_storage_boto_session(
        _settings(
            aws_access_key_id="AKIA_BEDROCK",
            aws_secret_access_key="bedrock",
            storage_access_key="AKIA_BUCKET",
            storage_secret_key="bucket",  # noqa: S106 - test fixture
            storage_region="eu-west-1",
        )
    )

    credentials = session.get_credentials()
    assert credentials is not None
    assert credentials.get_frozen_credentials().access_key == "AKIA_BUCKET"
    assert session.region_name == "eu-west-1"


def test_the_session_token_is_not_inherited_from_the_machine(
    machine_credentials: str,
) -> None:
    """A permanent key must not pick up the shared file's session token.

    This exact mix -- our access key, the machine's token -- would fail as
    an invalid-token error, which reads like a bad key rather than a
    credential-resolution problem.
    """
    del machine_credentials

    session = build_boto_session(
        _settings(aws_access_key_id="AKIAEXAMPLE", aws_secret_access_key="secret"),
        purpose="a test",
    )

    credentials = session.get_credentials()
    assert credentials is not None
    assert credentials.get_frozen_credentials().token is None
