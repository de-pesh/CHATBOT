"""Files models."""
from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field


# Where a file's async parse+index job (kb_worker) currently stands. A null
# vector_store_file_id alone can't distinguish "still queued" from "permanently failed" -
# this is what actually answers that.
class ProcessingStatus(str, Enum):
    PENDING = "pending"        # uploaded, job enqueued, not yet picked up
    PROCESSING = "processing"  # kb_worker is parsing/indexing it right now
    COMPLETED = "completed"    # extracted_text_path and vector_store_file_id are set
    FAILED = "failed"          # exhausted retries; see processing_error


class FileLifecycleStatus(str, Enum):
    ACTIVE = "ACTIVE"
    DISABLE = "DISABLE"
    INACTIVE = "INACTIVE"

# Base model for common attributes
class FileBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Name of the file")
    mimetype: str = Field(..., max_length=100, description="MIME type of the file")
    file_path: str = Field(..., description="Path where the file is stored")
    extracted_text_path: str | None = Field(None, description="Path to the plain-text version produced by the parser, used for indexing and catalog descriptions")

# Model for creating a new file (input)
class FileCreate(FileBase):
    pass

# Model for file data as stored in the database (includes DB-generated fields)
class FileInDB(FileBase):
    id: int = Field(..., description="Unique identifier of the file")
    vector_store_file_id: str | None = Field(None, description="ID of the document in the vector store, set once indexing completes")
    processing_status: ProcessingStatus = Field(ProcessingStatus.PENDING, description="Status of the async parse+index job")
    processing_error: str | None = Field(None, description="Failure reason, set only when processing_status is 'failed'")
    is_active: bool = Field(True, description="Indicates if the file is active")
    document_key: str | None = Field(None, description="The document this file is a version of (its name without extension or version tag)")
    version_number: int = Field(1, description="Which upload of this document this is: 1, 2, 3...")
    is_latest: bool = Field(True, description="Whether this is the newest ingested version, the one questions are answered from by default")
    version_label: str | None = Field(None, description="The version's own name, e.g. '3.2', from the file name or the upload")
    effective_date: date | None = Field(None, description="When this version took effect, if the uploader said")
    created_on: datetime = Field(..., description="Timestamp when the file record was created")
    created_by: str = Field(..., description="User who created the file record")
    updated_on: datetime | None = Field(None, description="Timestamp when the file record was last updated")
    updated_by: str | None = Field(None, description="User who last updated the file record")

# Model for API response (can be same as InDB or a subset)
class FileResponse(FileInDB):
    pass


# Lightweight polling response for GET /files/{id}/status - just the fields a
# caller checking on an in-flight upload needs, not the rest of the row.
class FileStatusResponse(BaseModel):
    id: int
    processing_status: ProcessingStatus
    processing_error: str | None = None
    vector_store_file_id: str | None = None
