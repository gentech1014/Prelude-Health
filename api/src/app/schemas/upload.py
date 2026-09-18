"""API schemas for the presigned-URL document and recording upload flow."""

from pydantic import BaseModel


class UploadUrlRequest(BaseModel):
    """Requested before a direct-to-storage upload."""

    content_type: str


class UploadUrlResponse(BaseModel):
    """A pre-signed URL the frontend can `PUT` the file to directly, and the
    storage key to report back once that upload finishes."""

    upload_url: str
    key: str


class StorageCompleteRequest(BaseModel):
    """Submitted once the frontend's direct-to-storage upload has finished.

    Shared by both the document and recording `.../complete` routes -- same
    shape, same meaning: "the bytes are at this key now."
    """

    storage_key: str
