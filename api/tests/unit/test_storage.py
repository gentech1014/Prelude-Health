"""Unit tests for the S3-backed object storage client.

Uses `moto` to intercept boto3 calls with an in-memory fake S3, so these
tests never touch a real AWS account.
"""

from urllib.parse import urlsplit

import boto3
import pytest
from moto import mock_aws

from app.core.config import Settings
from app.core.exceptions import StorageError
from app.integrations.storage import ObjectStorageClient

_BUCKET = "test-prescreening-bucket"
_REGION = "us-east-1"


def _settings() -> Settings:
    return Settings(
        _env_file=None,  # isolate from the developer's real .env -- see tests/conftest.py
        intake_link_secret="unused-but-required-secret-32-chars",  # noqa: S106 - test fixture
        booking_webhook_secret="unused-but-required-secret-32-chars",  # noqa: S106 - test fixture
        physician_api_key="unused-but-required-key-32-characters",  # noqa: S106 - test fixture
        aws_region=_REGION,
        storage_bucket_name=_BUCKET,
        # moto never validates these, but boto3 still requires credentials
        # to exist. Supplied explicitly so these tests do not quietly
        # depend on the developer's machine having AWS credentials at all.
        aws_access_key_id="testing",
        aws_secret_access_key="testing",  # noqa: S106 - moto placeholder
    )


@pytest.fixture
def moto_bucket():
    """A fake S3 bucket, live only for the duration of one test."""
    with mock_aws():
        boto3.client("s3", region_name=_REGION).create_bucket(Bucket=_BUCKET)
        yield


async def test_upload_stores_bytes_and_returns_an_s3_uri(moto_bucket: None) -> None:
    """A successful upload returns an `s3://bucket/key` URI."""
    client = ObjectStorageClient(_settings())

    uri = await client.upload("documents/sess_1/report.pdf", b"file-bytes", "application/pdf")

    assert uri == f"s3://{_BUCKET}/documents/sess_1/report.pdf"
    stored = boto3.client("s3", region_name=_REGION).get_object(
        Bucket=_BUCKET, Key="documents/sess_1/report.pdf"
    )
    assert stored["Body"].read() == b"file-bytes"
    assert stored["ServerSideEncryption"] == "AES256"


async def test_upload_uses_sse_kms_when_a_key_id_is_configured(moto_bucket: None) -> None:
    """A configured `storage_kms_key_id` switches encryption to SSE-KMS."""
    settings = _settings()
    settings.storage_kms_key_id = "arn:aws:kms:us-east-1:123456789012:key/test-key"
    client = ObjectStorageClient(settings)

    await client.upload("documents/sess_1/report.pdf", b"file-bytes", "application/pdf")

    stored = boto3.client("s3", region_name=_REGION).get_object(
        Bucket=_BUCKET, Key="documents/sess_1/report.pdf"
    )
    assert stored["ServerSideEncryption"] == "aws:kms"
    assert stored["SSEKMSKeyId"].endswith("test-key")


async def test_generate_upload_url_returns_a_putable_url(moto_bucket: None) -> None:
    """The presigned URL targets the right bucket/key and is a PUT URL."""
    client = ObjectStorageClient(_settings())

    url = await client.generate_upload_url("recordings/sess_1/call.webm", "video/webm")

    assert _BUCKET in url
    assert "recordings/sess_1/call.webm" in url


async def test_upload_wraps_a_missing_bucket_as_storage_error() -> None:
    """A real S3 failure (e.g. no such bucket) surfaces as `StorageError`, not a raw boto3 traceback."""
    with mock_aws():
        client = ObjectStorageClient(_settings())  # bucket was never created in this test

        with pytest.raises(StorageError):
            await client.upload("documents/sess_1/report.pdf", b"data", "application/pdf")


async def test_download_returns_bytes_and_content_type(moto_bucket: None) -> None:
    """A successful download returns both the body and the content-type S3
    actually stored -- the only record of it anywhere, since it is never
    persisted separately (see `ObjectStorageClient.download`'s docstring)."""
    client = ObjectStorageClient(_settings())
    await client.upload("documents/sess_1/xray.png", b"image-bytes", "image/png")

    data, content_type = await client.download("documents/sess_1/xray.png")

    assert data == b"image-bytes"
    assert content_type == "image/png"


async def test_download_wraps_a_missing_key_as_storage_error(moto_bucket: None) -> None:
    """Downloading a key that was never uploaded surfaces as `StorageError`,
    not a raw boto3 traceback."""
    client = ObjectStorageClient(_settings())

    with pytest.raises(StorageError):
        await client.download("documents/sess_1/never-uploaded.png")


def test_object_uri_builds_without_a_network_call() -> None:
    """`object_uri` is pure string construction -- no bucket needs to exist."""
    client = ObjectStorageClient(_settings())

    assert (
        client.object_uri("recordings/sess_1/call.webm")
        == f"s3://{_BUCKET}/recordings/sess_1/call.webm"
    )


# --------------------------------------------------------------------------
# The presigned URL's host, which a browser upload depends on
# --------------------------------------------------------------------------


def _regional_settings(**overrides: object) -> Settings:
    """Settings for a bucket in a region other than the default."""
    settings = _settings()
    return settings.model_copy(update={"storage_region": "ap-south-1", **overrides})


async def test_a_presigned_url_is_served_from_the_region_it_is_signed_for() -> None:
    """A browser upload fails outright when these two disagree.

    Under boto3's default `auto` addressing the presigner rewrites the host
    to virtual-hosted form and drops the region: the URL came back served
    from `bucket.s3.amazonaws.com` while signed for `ap-south-1`. S3
    answers a cross-region request to the global endpoint with a redirect,
    a redirect carries no CORS headers, and the browser reports it as a
    missing `Access-Control-Allow-Origin` -- which sends everyone looking
    at bucket policy instead of at the URL.
    """
    client = ObjectStorageClient(_regional_settings())

    url = await client.generate_upload_url("documents/sess_1/scan.pdf", "application/pdf")

    host = urlsplit(url).netloc
    assert host == f"{_BUCKET}.s3.ap-south-1.amazonaws.com"
    assert "%2Fap-south-1%2Fs3%2F" in url


async def test_a_download_url_is_addressed_the_same_way() -> None:
    """The doctor's Calendar links come from the same client and the same bug."""
    client = ObjectStorageClient(_regional_settings())

    url = await client.generate_download_url("documents/sess_1/scan.pdf")

    assert urlsplit(url).netloc == f"{_BUCKET}.s3.ap-south-1.amazonaws.com"


async def test_a_custom_endpoint_keeps_its_own_addressing() -> None:
    """`storage_endpoint_url` means an S3-compatible provider, not AWS.

    Many of those serve path-style only, so forcing virtual-hosted
    addressing would break the exact case the setting exists for.
    """
    client = ObjectStorageClient(_regional_settings(storage_endpoint_url="http://minio.test:9000"))

    url = await client.generate_upload_url("documents/sess_1/scan.pdf", "application/pdf")

    assert urlsplit(url).netloc == "minio.test:9000"
    assert urlsplit(url).path.startswith(f"/{_BUCKET}/")
