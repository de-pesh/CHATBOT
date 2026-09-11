"""Files models.

Merged from: file_models, version_models, label_mapping_wise_files_models, asset_models, asset_label_mappings_models, people_label_mappings_models.
"""
from datetime import datetime
from enum import Enum
from pydantic import AliasChoices, BaseModel, Field
from pydantic import EmailStr
from typing import List, Optional


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
class FileUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255, description="Name of the file")
    mimetype: Optional[str] = Field(None, max_length=100, description="MIME type of the file")
    file_path: Optional[str] = Field(None, description="Path where the file is stored")
    parsed_path: Optional[str] = Field(None, description="Path to the parsed version of the file, if applicable")
    file_type: Optional[FileType] = Field(None, description="Type of the file (framework or evidence)")

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

class VersionResponse(BaseModel):
    total_count: int = Field(..., description="Total count of rows available in the database")
    results: List[VersionInDB] = Field(..., description="List of all records fetched from the database")


# --- from label_mapping_wise_files_models.py ---
# app/models/evidence_mapping_models.py


# ==================================
# Evidence Mapping Models
# ==================================

class EvidenceMappingsCreatePayload(BaseModel):
    """
    Payload for creating one or more mappings between evidence files and a single control.
    All necessary foreign keys must be provided by the client.
    """
    assessment_id: int = Field(..., description="The ID of the assessment this mapping belongs to.")
    assessment_label_mapping_id: int = Field(
        ...,
        validation_alias=AliasChoices("assessment_label_mapping_id", "assessment_label_id"),
        description="The ID of the assessment label mapping (control mapping) to link files against."
    )
    file_ids: List[int] = Field(..., min_length=1, description="A list of unique evidence file IDs to map to the control.")

class EvidenceMappingsDeletePayload(BaseModel):
    """Payload for deleting one or more specific evidence-to-control mappings by their unique IDs."""
    mapping_ids: List[int] = Field(..., min_length=1, description="A list of unique evidence mapping IDs (label_wise_files.id) to be deleted.")

class EvidenceMappingResponse(BaseModel):
    """
    Complete response model for a single evidence-to-control link, including all DB fields.
    This corresponds to a row in the `temp_label_mapping_wise_files` table.
    """
    id: int
    assessment_id: int
    assessment_label_mapping_id: int
    file_id: int
    is_active: bool
    created_on: datetime
    created_by: str
    updated_on: Optional[datetime] = None
    updated_by: Optional[str] = None

    class Config:
        from_attributes = True


# --- from asset_models.py ---
# Base model for common attributes
class AssetBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Name of the asset")
    description: Optional[str] = Field(None, description="Description of the asset")
    asset_type_id: int = Field(..., description="ID of the asset type this asset belongs to")


# Model for creating a new asset (input)
class AssetCreate(BaseModel):
    data: List[AssetBase] = Field(..., description="List of Assets")


# Model for updating an existing asset (input)
class AssetUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255, description="Name of the asset")
    description: Optional[str] = Field(None, decription="Description of the asset")
    asset_type_id: Optional[int] = Field(None, description="ID of the asset type this asset belongs to")


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
class AssetResponse(BaseModel):
    total_count: int = Field(..., description="Total count of rows available in the database")
    results: List[AssetInDB] = Field(..., description="List of all records fetched from the database")


# Base model for common attributes
class AssetTypeBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Name of the asset type")
    description: Optional[str] = Field(None, description="Description of the asset type")


# Model for creating a new asset type (input)
class AssetTypeCreate(BaseModel):
    organization_id: int = Field(..., description="ID of the organization this asset type belongs to")
    data: List[AssetTypeBase] = Field(..., description="List of asset types")


# Model for updating an existing asset type (input)
class AssetTypeUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255, description="Name of the asset type")
    description: Optional[str] = Field(None, description="Description of the asset type")


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
class AssetTypeResponse(BaseModel):
    total_count: int = Field(..., description="Total count of rows available in the database")
    results: List[AssetTypeInDB] = Field(..., description="List of all records fetched from the database")


# --- from asset_label_mappings_models.py ---
class AssetLabelMappingCreate(BaseModel):
    """Payload to create one or more Asset-to-Control links."""
    asset_ids: List[int] = Field(..., min_length=1, description="A list of unique asset IDs to tag to the control.")


class AssetLabelMappingResponse(BaseModel):
    """Response model for a single, successfully created Asset-to-Control link."""
    id: int
    asset_id: int
    assessment_label_mapping_id: int
    is_active: bool
    created_on: datetime
    created_by: str
    updated_on: Optional[datetime] = None
    updated_by: Optional[str] = None
    
    class Config:
        from_attributes = True


# --- from people_label_mappings_models.py ---
class PeopleLabelMappingCreate(BaseModel):
    """Payload to create one or more Person-to-Control links."""
    emails: List[EmailStr] = Field(..., min_length=1, description="A list of user emails to tag to the control.")

class PeopleLabelMappingResponse(BaseModel):
    """Response model for a single, successfully created Person-to-Control link."""
    id: int
    email: EmailStr
    assessment_label_mapping_id: int
    is_active: bool
    created_on: datetime
    created_by: str
    updated_on: Optional[datetime] = None
    updated_by: Optional[str] = None

    class Config:
        from_attributes = True
