"""One-off: push the question-bank JSON export into the Moss index.

Isolated from the Mongo seed path on purpose -- this reads
`moss_question_bank.json` (generated from the same corpus as
`seed_question_bank.py`) and pushes it straight to Moss, so populating the
semantic index never depends on Mongo being reachable.

Run once per fresh Moss index: `python scripts/seed_moss_index.py`.
Safe to re-run -- `add_docs` upserts by document id.
"""

import asyncio
import json
from pathlib import Path

from moss import DocumentInfo, MossClient, MutationOptions

from app.core.config import get_settings

_JSON_PATH = Path(__file__).resolve().parents[3] / "moss_question_bank.json"


async def _seed() -> None:
    settings = get_settings()
    if not settings.moss_project_id or not settings.moss_project_key:
        raise SystemExit("MOSS_PROJECT_ID / MOSS_PROJECT_KEY are not set in .env")

    records = json.loads(_JSON_PATH.read_text(encoding="utf-8"))
    documents = [
        DocumentInfo(id=record["id"], text=record["text"], metadata={"category": record["category"]})
        for record in records
    ]

    client = MossClient(settings.moss_project_id, settings.moss_project_key)
    index = settings.moss_question_index

    try:
        result = await client.add_docs(index, documents, MutationOptions(upsert=True))
        print(f"add_docs: job_id={result.job_id}")
    except Exception as exc:  # noqa: BLE001 -- index may not exist yet, fall back to create
        print(f"add_docs failed ({exc!r}), trying create_index instead")
        result = await client.create_index(index, documents)
        print(f"create_index: job_id={result.job_id}")

    status = await client.get_job_status(result.job_id)
    print(f"job status: {status}")
    print(f"pushed {len(documents)} documents to Moss index '{index}'")


if __name__ == "__main__":
    asyncio.run(_seed())
