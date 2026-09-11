"""Files models.

Merged from file_models and version_models. The asset, label-mapping and
people-mapping models went with the assessment domain.
"""
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field
from typing import Optional


# --- from file_models.py ---
class FileType(str, Enum):
    FRAMEWORK = "framework"
    EVIDENCE = "evidence"

# Base model for common attributes
class FileBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Name of the file")
    mimetype: str = Field(..., max_length=100, description="MIME type of the file")
    file_path: str = Field(..., description="Path where the file is stored")
    parsed_path: Optional[str] = Field(None, description="Path to the parsed version of the file, if applicable")
    file_type: FileType = Field(..., description="Type of the file (framework or evidence)")

# Model for creating a new file (input)
class FileCreate(FileBase):
    assessment_id: Optional[int] = Field(None, description="The ID of the assessment to link the file to")
    framework_id: Optional[int] = Field(None, description="The ID of the framework to link the file to")

# Model for updating an existing file (input)
# Model for file data as stored in the database (includes DB-generated fields)
class FileInDB(FileBase):
    id: int = Field(..., description="Unique identifier of the file")
    kb_file_id: Optional[str] = Field(None, description="ID of the corresponding document in the chatbot's knowledge base, if it was indexed")
    is_active: bool = Field(True, description="Indicates if the file is active")
    created_on: datetime = Field(..., description="Timestamp when the file record was created")
    created_by: str = Field(..., description="User who created the file record")
    updated_on: Optional[datetime] = Field(None, description="Timestamp when the file record was last updated")
    updated_by: Optional[str] = Field(None, description="User who last updated the file record")

    class Config:
        from_attributes = True

# Model for API response (can be same as InDB or a subset)
class FileResponse(FileInDB):
    pass


# Model for a document living in the chatbot's knowledge base. Its identifier is
# the vector-store document ID (a UUID string), which is distinct from the
# integer `FileInDB.id` primary key of the SQL file record.
class KBDocumentResponse(BaseModel):
    file_id: str = Field(..., description="Knowledge-base document identifier (UUID string, not the SQL file ID)")
    file_name: Optional[str] = Field(None, description="Original name of the indexed document")
    chunk_count: int = Field(..., description="Number of indexed chunks belonging to this document")


# --- from version_models.py ---
class VersionBase(BaseModel):
    framework_id: int = Field(..., description="ID of the framework this version belongs to")
    number: int = Field(..., description="Version number")


class VersionInDB(VersionBase):
    id: int = Field(..., description="Unique identifier of the version")
    in_use: bool = Field(True, description="Indicates if the version is in use")
    is_active: bool = Field(True, description="Indicates if the version is active")
    created_on: datetime = Field(..., description="Timestamp when the version was created")
    created_by: str = Field(..., description="User who created the version")
    updated_on: Optional[datetime] = Field(None, description="Timestamp when the version was last updated")
    updated_by: Optional[str] = Field(None, description="User who last updated the version")

    class Config:
        from_attributes = True

# --- from label_mapping_wise_files_models.py ---
# app/models/evidence_mapping_models.py


# ==================================
# Evidence Mapping Models
# ==================================

# --- from asset_models.py ---
# Base model for common attributes
class AssetBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Name of the asset")
    description: Optional[str] = Field(None, description="Description of the asset")
    asset_type_id: int = Field(..., description="ID of the asset type this asset belongs to")


# Model for creating a new asset (input)
# Model for updating an existing asset (input)
# Model for asset data as stored in the database (includes DB-generated fields)
class AssetInDB(AssetBase):
    id: int = Field(..., description="Unique identifier of the asset")
    is_active: bool = Field(True, description="Indicates if the asset is active")
    created_on: datetime = Field(..., description="Timestamp when the asset was created")
    created_by: str = Field(..., description="User who created the asset")
    updated_on: Optional[datetime] = Field(None, description="Timestamp when the asset was last updated")
    updated_by: Optional[str] = Field(None, description="User who last updated the asset")

    class Config:
        from_attributes = True


# Model for API response (can be same as InDB or a subset)
# Base model for common attributes
class AssetTypeBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Name of the asset type")
    description: Optional[str] = Field(None, description="Description of the asset type")


# Model for creating a new asset type (input)
# Model for updating an existing asset type (input)
# Model for asset type data as stored in the database (includes DB-generated fields)
class AssetTypeInDB(AssetTypeBase):
    id: int = Field(..., description="Unique identifier of the asset type")
    organization_id: int = Field(..., description="ID of the organization this asset type belongs to")
    is_active: bool = Field(True, description="Indicates if the asset type is active")
    created_on: datetime = Field(..., description="Timestamp when the asset type was created")
    created_by: str = Field(..., description="User who created the asset type")
    updated_on: Optional[datetime] = Field(None, description="Timestamp when the asset type was last updated")
    updated_by: Optional[str] = Field(None, description="User who last updated the asset type")

    class Config:
        from_attributes = True


# Model for API response (can be same as InDB or a subset)
# --- from asset_label_mappings_models.py ---
# --- from people_label_mappings_models.py ---