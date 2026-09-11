"""Framework models.

Merged from: framework_models, framework_tree_models, framework_configuration_models, configuration_models.
"""
from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel, Field
from typing import List, Optional


# --- from framework_models.py ---
# Base model for common attributes
class FrameworkBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Name of the framework")
    description: Optional[str] = Field(None, description="Description of the framework")


# Model for creating a new framework (input)
class FrameworkCreate(FrameworkBase):
    pass


# Model for updating an existing framework (input)
class FrameworkUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255, description="Name of the framework")
    description: Optional[str] = Field(None, description="Description of the framework")


# Model for framework data as stored in the database (includes DB-generated fields)
class FrameworkInDB(FrameworkBase):
    id: int = Field(..., description="Unique identifier of the framework")
    is_active: bool = Field(True, description="Indicates if the framework is active")
    created_on: datetime = Field(..., description="Timestamp when the framework was created")
    created_by: str = Field(..., description="User who created the framework")
    updated_on: Optional[datetime] = Field(None, description="Timestamp when the framework was last updated")
    updated_by: Optional[str] = Field(None, description="User who last updated the framework")

    class Config:
        from_attributes = True


# Model for API response (can be same as InDB or a subset)
class FrameworkResponse(BaseModel):
    total_count: int = Field(..., description="Total count of rows available in the database")
    results: List[FrameworkInDB] = Field(..., description="List of all records fetched from the database")


# --- from framework_tree_models.py ---
# Base model for common attributes
class FrameworkNode(BaseModel):
    node_id: str = Field(..., description="Unique Identifier of the Framework Node")
    label_id: str = Field(..., description="Unique Label of the Framework Node")
    name: str = Field(..., description="Name of the Framework Node")
    description: Optional[str] = Field(None, description="Description of the Framework Node")
    guidelines: Optional[str] = Field(None, description="Guidelines of the Framework Node")
    correspoding_question: Optional[str] = Field(None, description="Corresponding Question of the Framework Node")
    label_type: int = Field(..., description="Label type ID of the Framework Node")
    children: Optional[List[FrameworkNode]] = Field(..., description="Children of the Framework Node")
    criterias: Optional[List[str]] = Field(None, description="LLM-generated evaluation criteria for this node")
    created_on: datetime = Field(..., description="Date when the framework node was created")
    created_by: str = Field(..., description="Person who created the framework node")
    updated_on: Optional[datetime] = Field(None, description="Date when the framework node was updated")
    updated_by: Optional[str] = Field(None, description="Person who updated the framework node")


# Model for response of tree structure
class FrameworkTreeResponse(BaseModel):
    framework_name: str = Field(..., description="Name of the framework")
    file_id: int = Field(..., description="File to which this tree belongs to")
    file_name: str = Field(..., description="Name of the file to which this tree belongs to")
    tree: List[FrameworkNode] = Field(..., description="Tree structure of the controls")


# --- from framework_configuration_models.py ---
# Base model for common attributes of an framework configuration
class FrameworkConfigurationBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Name of the Framework Configuration")
    description: Optional[str] = Field(None, description="Description of the Framework Configuration")


class Hierarchy(FrameworkConfigurationBase):
    id: Optional[int] = Field(None, description="Unique identifier of the Framework Configuration")
    children: Optional[List[Hierarchy]] = Field(default_factory=list, description="Child units of the Framework Configuration")


# Model for framework configuration data received in the request payload for creation
class FrameworkConfigurationCreatePayload(BaseModel):
    framework_id: int = Field(..., description="ID of the framework this configuration belongs to")
    hierarchy: Hierarchy = Field(..., description="Hierarchy of the framework configuration")


# Model for framework configuration data received in the request payload for update
class FrameworkConfigurationUpdatePayload(FrameworkConfigurationBase):
    name: Optional[str] = Field(None, min_length=1, max_length=255, description="Name of the Framework Configuration")


# Model for framework configuration data as stored in the database (includes DB-generated fields)
class FrameworkConfigurationInDB(FrameworkConfigurationBase):
    id: int = Field(..., description="Unique identifier of the framework configuration")
    framework_id: int = Field(..., description="ID of the framework this configuration belongs to")
    parent_id: Optional[int] = Field(None, description="ID of the parent framework configuration")
    is_active: bool = Field(True, description="Indicates if the framework configuration is active")
    created_on: datetime = Field(..., description="Timestamp when the framework configuration was created")
    created_by: str = Field(..., description="User who created the framework configuration")
    updated_on: Optional[datetime] = Field(None, description="Timestamp when the framework configuration was last updated")
    updated_by: Optional[str] = Field(None, description="User who last updated the framework configuration")

    class Config:
        from_attributes = True


class FrameworkConfigurationResponse(FrameworkConfigurationBase):
    id: int = Field(..., description="Unique identifier of the framework configuration")
    children: Optional[List[FrameworkConfigurationResponse]] = Field(default_factory=list, description="Child units of the framework configuration")
    is_active: bool = Field(True, description="Indicates if the framework configuration is active")
    created_on: datetime = Field(..., description="Timestamp when the framework configuration was created")
    created_by: str = Field(..., description="User who created the framework configuration")
    updated_on: Optional[datetime] = Field(None, description="Timestamp when the framework configuration was last updated")
    updated_by: Optional[str] = Field(None, description="User who last updated the framework configuration")


# Model for API response (can be same as InDB or a subset)
class FrameworkConfigurationsResponse(FrameworkConfigurationCreatePayload):
    framework_id: int = Field(..., description="ID of the framework this configuration belongs to")
    hierarchy: FrameworkConfigurationResponse = Field(..., description="Hierarchy of the framework configuration")


# --- from configuration_models.py ---
# Base model for common attributes
class ConfigurationBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Name of the configuration")
    description: Optional[str] = Field(None, description="Description of the configuration")
    is_active: bool = Field(True, description="Indicates if the configuration is active")


# Model for creating a new configuration (input)
class ConfigurationCreate(ConfigurationBase):
    parent_name: Optional[str] = Field(None, description="Name of the parent configuration, used to establish hierarchy.")


# Model for updating an existing configuration (input)
class ConfigurationUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255, description="Name of the configuration")
    description: Optional[str] = Field(None, description="Description of the configuration")
    parent_id: Optional[int] = Field(None, description="ID of the parent configuration")
    is_active: Optional[bool] = Field(None, description="Indicates if the configuration is active")


# Model for configuration data as stored in the database
class ConfigurationInDB(ConfigurationBase):
    id: int = Field(..., description="Unique identifier of the configuration")
    parent_id: Optional[int] = Field(None, description="ID of the parent configuration")
    created_on: datetime = Field(..., description="Timestamp when the configuration was created")
    created_by: str = Field(..., description="User who created the configuration")
    updated_on: Optional[datetime] = Field(None, description="Timestamp when the configuration was last updated")
    updated_by: Optional[str] = Field(None, description="User who last updated the configuration")

    class Config:
        from_attributes = True


# Model for API response, including the parent's name
class ConfigurationResponse(ConfigurationInDB):
    parent_name: Optional[str] = Field(None, description="Name of the parent configuration")
