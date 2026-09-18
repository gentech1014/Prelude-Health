"""Presigned-URL document + recording upload flow.

Both artifacts follow the same shape: the frontend asks here for a
presigned URL, uploads the bytes straight to object storage (never through
this API -- see `app.agents.tools.upload`'s docstring on why documents work
this way, and `app.services.video_service` for the recording's equivalent),
then calls back here to confirm completion.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import (
    get_session_service,
    get_storage_client,
    get_video_service,
    require_session_cookie,
)
from app.integrations.storage import ObjectStorageClient
from app.schemas.session import SessionStatusResponse
from app.schemas.upload import StorageCompleteRequest, UploadUrlRequest, UploadUrlResponse
from app.services.session_service import SessionService
from app.services.video_service import VideoService

router = APIRouter(
    prefix="/sessions/{session_id}",
    tags=["uploads"],
    dependencies=[Depends(require_session_cookie)],
)


@router.post("/documents/upload-url", response_model=UploadUrlResponse)
async def create_document_upload_url(
    session_id: str,
    payload: UploadUrlRequest,
    storage: Annotated[ObjectStorageClient, Depends(get_storage_client)],
) -> UploadUrlResponse:
    """Return a presigned URL for the patient's supporting-document upload."""
    key = f"documents/{session_id}/{uuid.uuid4().hex}"
    upload_url = await storage.generate_upload_url(key, payload.content_type)
    return UploadUrlResponse(upload_url=upload_url, key=key)


@router.post("/documents/complete", response_model=SessionStatusResponse)
async def complete_document_upload(
    session_id: str,
    payload: StorageCompleteRequest,
    session_service: Annotated[SessionService, Depends(get_session_service)],
) -> SessionStatusResponse:
    """Link the uploaded document to its session.

    Also triggers best-effort delivery of the document link onto the
    doctor's Google Calendar event, see `SessionService.attach_document`.
    """
    session = await session_service.attach_document(session_id, payload.storage_key)
    return SessionStatusResponse.from_session(session)


@router.post("/recording/upload-url", response_model=UploadUrlResponse)
async def create_recording_upload_url(
    session_id: str,
    payload: UploadUrlRequest,
    storage: Annotated[ObjectStorageClient, Depends(get_storage_client)],
) -> UploadUrlResponse:
    """Return a presigned URL for the call's screen-recording upload."""
    key = f"recordings/{session_id}/{uuid.uuid4().hex}"
    upload_url = await storage.generate_upload_url(key, payload.content_type)
    return UploadUrlResponse(upload_url=upload_url, key=key)


@router.post("/recording/complete", response_model=SessionStatusResponse)
async def complete_recording_upload(
    session_id: str,
    payload: StorageCompleteRequest,
    video_service: Annotated[VideoService, Depends(get_video_service)],
) -> SessionStatusResponse:
    """Link the uploaded recording to its session and move it to VIDEO_READY.

    Also triggers best-effort delivery of the recording link onto the
    doctor's Google Calendar event, see `VideoService.attach_recording`.
    """
    session = await video_service.attach_recording(session_id, payload.storage_key)
    return SessionStatusResponse.from_session(session)
