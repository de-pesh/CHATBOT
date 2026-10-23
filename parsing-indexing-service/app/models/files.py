"""Files models (subset used by the worker)."""
from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field


class ProcessingStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class FileBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    mimetype: str = Field(..., max_length=100)
    file_path: str
    extracted_text_path: str | None = None


class FileCreate(FileBase):
    pass


class FileInDB(FileBase):
    id: int
    vector_store_file_id: str | None = None
    processing_status: ProcessingStatus = ProcessingStatus.PENDING
    processing_error: str | None = None
    is_active: bool = True
    document_key: str | None = None
    version_number: int = 1
    is_latest: bool = True
    version_label: str | None = None
    effective_date: date | None = None
    created_on: datetime
    created_by: str
    updated_on: datetime | None = None
    updated_by: str | None = None
