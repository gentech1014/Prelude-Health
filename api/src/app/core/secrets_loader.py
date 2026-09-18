"""Production secrets bootstrap: fetch from AWS Secrets Manager before `Settings` loads.

Runs once, at process cold start, before anything else -- `Settings` reads
plain environment variables, so this only ever needs to populate
`os.environ` and get out of the way. Local/dev environments are untouched:
this no-ops immediately unless `APP_ENV=production`, so `.env`-based
config keeps working exactly as it does today.

Deliberately reads `APP_ENV` straight from `os.environ` rather than via
`Settings` -- `Settings` does not exist yet at the point this runs, since
constructing it is the very thing this module needs to happen first.
"""

import json
import os
import tempfile

import boto3
import structlog

log = structlog.get_logger(__name__)


def load_secrets_into_env() -> None:
    """Populate `os.environ` from AWS Secrets Manager, if configured for production.

    No-ops unless `APP_ENV=production`. Otherwise fetches the JSON secret
    blob named by `SECRETS_MANAGER_SECRET_ID`, and for each key/value pair
    calls `os.environ.setdefault` -- never overwrites a value an operator
    already set directly in the environment, so a one-off manual override
    still works.

    Raises on failure rather than swallowing it: a production boot that
    cannot reach its own secrets must not silently start up
    misconfigured. Same philosophy as the question-bank preload in
    `app.main.lifespan` failing loudly on a missing category.

    A `GOOGLE_SERVICE_ACCOUNT_JSON` key in the blob (the service-account
    key content, not a file path) is materialized to a temp file and
    exposed as `GOOGLE_SERVICE_ACCOUNT_FILE`, so `GoogleCalendarClient`
    needs no changes to consume a secret sourced this way.
    """
    if os.environ.get("APP_ENV") != "production":
        return

    secret_id = os.environ["SECRETS_MANAGER_SECRET_ID"]
    region = os.environ.get("AWS_REGION", "us-east-1")

    client = boto3.client("secretsmanager", region_name=region)
    response = client.get_secret_value(SecretId=secret_id)
    values: dict[str, str] = json.loads(response["SecretString"])

    google_key_json = values.pop("GOOGLE_SERVICE_ACCOUNT_JSON", None)
    if google_key_json and "GOOGLE_SERVICE_ACCOUNT_FILE" not in os.environ:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(google_key_json)
            os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"] = handle.name

    for key, value in values.items():
        os.environ.setdefault(key, value)

    log.info("secrets_loaded_from_secrets_manager", secret_id=secret_id)
