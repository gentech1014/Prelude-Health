"""Apply s3-cors.json to the upload bucket. Run once whenever the CORS rules change.

Uses the same credential/region resolution as `app.integrations.storage`, so
it works with whatever `.env` already has configured -- nothing to pass in.
"""

import json
from pathlib import Path

import boto3

from app.core.config import get_settings

CORS_FILE = Path(__file__).resolve().parents[1] / "s3-cors.json"


def main() -> None:
    settings = get_settings()
    client = boto3.client(
        "s3",
        region_name=settings.storage_region or settings.aws_region,
        aws_access_key_id=settings.storage_access_key or settings.aws_access_key_id or None,
        aws_secret_access_key=settings.storage_secret_key or settings.aws_secret_access_key or None,
        endpoint_url=settings.storage_endpoint_url or None,
    )
    cors = json.loads(CORS_FILE.read_text())
    client.put_bucket_cors(Bucket=settings.storage_bucket_name, CORSConfiguration=cors)
    print(f"CORS applied to bucket: {settings.storage_bucket_name}")


if __name__ == "__main__":
    main()
