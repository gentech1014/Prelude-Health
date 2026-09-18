"""Object storage client for uploaded documents and screen recordings.

Both the patient-uploaded supporting document (X-ray, lab result) and the
patient's screen recording land here, out-of-band from the live audio
session -- see `app.agents.tools.upload` for why documents are never pushed
into the live session itself.
"""

import asyncio
from typing import Any

from botocore.client import Config
from botocore.exceptions import ClientError

from app.core.aws import build_storage_boto_session
from app.core.config import Settings
from app.core.exceptions import StorageError

S3Client = Any
"""boto3 is dynamically generated and ships no client type, so there is
nothing more precise to annotate the cached client with."""


class ObjectStorageClient:
    """Thin wrapper around an S3 (or S3-compatible) bucket.

    boto3 is synchronous, so every call is pushed onto a worker thread with
    `asyncio.to_thread` -- the same discipline `app.db.mongo` documents for
    Motor: this client's callers run on the same event loop that may still
    be carrying live-call traffic for other sessions, and a blocking network
    call here must never stall them.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._bucket = settings.storage_bucket_name
        self._kms_key_id = settings.storage_kms_key_id
        self._cached_client: S3Client | None = None

    @property
    def _client(self) -> S3Client:
        """The S3 client, built on first actual use.

        Lazy on purpose. This class is constructed per request as part of
        `SessionService`, including on routes that never touch storage
        (consent, status, the WS ticket). Building the client eagerly would
        make every one of those fail on a deployment with no storage
        credentials, over a bucket they were never going to read.

        `signature_version='s3v4'` is pinned explicitly: without it, boto3's
        default signer for a region that does not match the bucket's actual
        region (a real possibility here -- S3 has no region requirement the
        way Nova Sonic does) can fall back to the legacy SigV2
        query-string scheme, which most regions now reject outright
        ("use AWS4-HMAC-SHA256"). SigV4 works everywhere.

        `addressing_style='virtual'` is pinned for the same class of reason,
        and fixes a real bug. The client's own endpoint resolves correctly
        to `s3.<region>.amazonaws.com`, but under the default `auto` style
        the *presigner* rewrites the host to virtual-hosted form and drops
        the region while doing it -- producing a URL served from
        `bucket.s3.amazonaws.com` yet signed for `ap-south-1`. S3 answers a
        cross-region request to the global endpoint with a redirect, and a
        redirect carries no CORS headers, so a browser upload fails with
        "No 'Access-Control-Allow-Origin' header" no matter how the bucket
        is configured. Verified against this deployment's own bucket.

        Only for real S3. A custom `storage_endpoint_url` means an
        S3-compatible provider (MinIO and friends), and many of those serve
        path-style only -- forcing virtual-hosted addressing there would
        break the case the setting exists for.
        """
        if self._cached_client is None:
            endpoint_url = self._settings.storage_endpoint_url or None
            config = (
                Config(signature_version="s3v4")
                if endpoint_url
                else Config(signature_version="s3v4", s3={"addressing_style": "virtual"})
            )
            # Credentials come from this service's own configuration, never
            # from boto3's ambient chain -- see `app.core.aws`.
            self._cached_client = build_storage_boto_session(self._settings).client(
                "s3",
                endpoint_url=endpoint_url,
                config=config,
            )
        return self._cached_client

    async def upload(self, key: str, data: bytes, content_type: str) -> str:
        """Upload bytes under `key` and return the storage URI.

        Always requests server-side encryption explicitly rather than
        relying solely on the bucket's own default-encryption policy: KMS
        (`aws:kms`, with `storage_kms_key_id`) when a key is configured,
        else SSE-S3 (`AES256`) -- still encrypted at rest either way. This
        method is the server-side write path only (the generated PDF); the
        client-facing presigned PUT in `generate_upload_url` intentionally
        does not bake in the same params -- see that method's docstring.
        """
        encryption: dict[str, str] = (
            {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": self._kms_key_id}
            if self._kms_key_id
            else {"ServerSideEncryption": "AES256"}
        )
        try:
            await asyncio.to_thread(
                self._client.put_object,
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                **encryption,
            )
        except ClientError as exc:
            raise StorageError("upload", exc) from exc
        return f"s3://{self._bucket}/{key}"

    async def generate_upload_url(self, key: str, content_type: str) -> str:
        """Return a pre-signed URL the frontend can upload directly to.

        Preferred over proxying the file through this API: per
        `app.agents.tools.upload`'s own docstring, the file is meant to go
        straight to object storage, not through the backend.

        Deliberately does not bake `ServerSideEncryption`/`SSEKMSKeyId`
        into the signed params here: a presigned PUT requires the caller's
        actual request headers to match whatever was signed, and there is
        no patient-upload frontend built yet to guarantee that contract.
        Enable S3 bucket-default encryption instead (a one-time bucket
        setting, outside this code) so objects landing via this URL are
        still encrypted at rest without any client-side header requirement.
        """
        try:
            return await asyncio.to_thread(
                self._client.generate_presigned_url,
                "put_object",
                Params={"Bucket": self._bucket, "Key": key, "ContentType": content_type},
                ExpiresIn=900,
            )
        except ClientError as exc:
            raise StorageError("generate_upload_url", exc) from exc

    async def generate_download_url(self, key: str, expires_in: int = 518_400) -> str:
        """Return a pre-signed GET URL for an object already in the bucket.

        Defaults to 6 days, not 7: AWS hard-caps a SigV4 presigned URL's
        `X-Amz-Expires` at *under* 604800 seconds (one week) for these
        credentials -- confirmed live, where the boundary value 604800
        itself was rejected with `AuthorizationQueryParametersError`
        ("X-Amz-Expires must be less than a week"). This is enforced by S3
        itself, not by this client, and `moto`'s fake S3 does not reproduce
        it, so only a real request against the real bucket ever catches it.
        Used for the report, document, and recording links appended to the
        doctor's Calendar event -- the `s3://...` URI `object_uri` returns
        is an internal reference, useless in a browser; this is what makes
        the Calendar entry actually clickable.
        """
        try:
            return await asyncio.to_thread(
                self._client.generate_presigned_url,
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires_in,
            )
        except ClientError as exc:
            raise StorageError("generate_download_url", exc) from exc

    async def download(self, key: str) -> tuple[bytes, str]:
        """Fetch an object's bytes and its stored content-type.

        The content-type is never persisted separately anywhere in this
        codebase: `generate_upload_url` signs the presigned PUT with the
        frontend-declared `ContentType`, which means S3 rejects the PUT
        outright unless the actual request header matches -- so whatever
        S3 reports back here is already reliable, with no separate DB
        field or frontend contract change needed to know it.
        """
        try:
            response = await asyncio.to_thread(
                self._client.get_object, Bucket=self._bucket, Key=key
            )
        except ClientError as exc:
            raise StorageError("download", exc) from exc
        body = await asyncio.to_thread(response["Body"].read)
        return body, response.get("ContentType", "application/octet-stream")

    def object_uri(self, key: str) -> str:
        """Build the storage URI for an object already uploaded under `key`.

        No network call -- used once the frontend confirms a presigned-URL
        upload has completed, so there is nothing left to verify here.
        """
        return f"s3://{self._bucket}/{key}"
