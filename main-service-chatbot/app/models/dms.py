"""Pydantic models for the DMS (Document Management System) tables."""
from datetime import datetime

from pydantic import BaseModel, Field


class DmsDocumentVersion(BaseModel):
    id: int
    dms_document_id: int
    version_number: float
    status: str
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    pvc_path: str | None = Field(None, description="Store-relative path on the shared PVC, e.g. 'chatbot-kb/abc.pdf'")
    document_source_type: str
    is_archived: bool
    creation_date: datetime | None = None
    created_by: str | None = None


class DmsDocumentVersionDetail(DmsDocumentVersion):
    """Includes the parent document's metadata for a single-version lookup."""
    markdown: str | None = None
    document_name: str | None = None
    document_title: str | None = None
    dms_category_id: int | None = None
    sensitivity: str | None = None


class DmsDocument(BaseModel):
    id: int
    dms_category_id: int
    name: str
    code: str | None = None
    title: str
    description: str | None = None
    icon: str | None = None
    current_version_id: int | None = None
    sensitivity: str | None = None
    is_archived: bool
    status: int | None = None
    creation_date: datetime | None = None
    created_by: str | None = None
    current_pvc_path: str | None = Field(
        None, description="PVC path of the current version (shortcut join, avoids an extra call)."
    )
    current_version_number: float | None = None


class PaginatedDmsDocuments(BaseModel):
    total_records: int
    page_number: int
    page_size: int
    data: list[DmsDocument]
