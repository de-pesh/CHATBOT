"""Org models.

Merged from: organization_models, org_unit_models, org_unit_value_models, org_settings_models, org_gate_settings_models, org_fixed_actioner_models, org_unit_label_mappings_models.
"""
from __future__ import annotations
from datetime import datetime
from datetime import timezone
from pydantic import BaseModel, Field
from pydantic import EmailStr
from typing import List, Optional


# --- from organization_models.py ---
# Base model for common attributes
class OrganizationBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Name of the organization")
    description: Optional[str] = Field(None, description="Description of the organization")


# Model for creating a new organization (input)
class OrganizationCreate(OrganizationBase):
    pass


# Model for updating an existing organization (input)
class OrganizationUpdate(OrganizationBase):
    name: Optional[str] = Field(None, min_length=1, max_length=255, description="Name of the organization")


# Model for organization data as stored in the database (includes DB-generated fields)
class OrganizationInDB(OrganizationBase):
    id: int = Field(..., description="Unique identifier of the organization")
    is_active: bool = Field(True, description="Indicates if the organization is active")
    created_on: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Timestamp when the organization was created")
    created_by: str = Field(default="ADMIN", description="User who created the organization")
    updated_on: Optional[datetime] = Field(None, description="Timestamp when the organization was last updated")
    updated_by: Optional[str] = Field(None, description="User who last updated the organization")

    class Config:
        from_attributes = True

class OrganizationResponse(BaseModel):
    total_count: int = Field(..., description="Total count of rows available in the database")
    results: List[OrganizationInDB] = Field(..., description="List of all records fetched from the database")
    


# --- from org_unit_models.py ---
# Base model for common attributes of an org unit
class OrgUnitBase(BaseModel):
    id: Optional[int] = Field(None, description="Unique identifier of the organization unit")
    name: str = Field(..., min_length=1, max_length=255, description="Name of the organization unit")


class OrgUnitHierarchy(OrgUnitBase):
    children: Optional[List[OrgUnitHierarchy]] = Field(default_factory=list, description="Child units of the organization unit")


# Model for organization unit data received in the request payload for creation
class OrgUnitCreatePayload(BaseModel):
    organization_id: int = Field(..., description="ID of the organization this unit belongs to")
    hierarchy: OrgUnitHierarchy = Field(..., description="OrgUnitHierarchy of the organization")


# Model for organization unit data received in the request payload for update
class OrgUnitUpdatePayload(OrgUnitBase):
    pass


# Model for organization unit data as stored in the database (includes DB-generated fields)
class OrgUnitInDB(OrgUnitBase):
    id: int = Field(..., description="Unique identifier of the organization unit")
    organization_id: int = Field(..., description="ID of the organization this unit belongs to")
    parent_id: Optional[int] = Field(None, description="ID of the parent organization unit")
    is_active: bool = Field(True, description="Indicates if the organization unit is active")
    created_on: datetime = Field(..., description="Timestamp when the organization unit was created")
    created_by: str = Field(..., description="User who created the organization unit")
    updated_on: Optional[datetime] = Field(None, description="Timestamp when the organization unit was last updated")
    updated_by: Optional[str] = Field(None, description="User who last updated the organization unit")

    class Config:
        from_attributes = True


class OrgUnitResponse(OrgUnitBase):
    children: Optional[List[OrgUnitResponse]] = Field(default_factory=list, description="Child units of the organization unit")
    is_active: bool = Field(True, description="Indicates if the organization unit is active")
    created_on: datetime = Field(..., description="Timestamp when the organization unit was created")
    created_by: str = Field(..., description="User who created the organization unit")
    updated_on: Optional[datetime] = Field(None, description="Timestamp when the organization unit was last updated")
    updated_by: Optional[str] = Field(None, description="User who last updated the organization unit")


# Model for API response (can be same as InDB or a subset)
class OrgUnitsResponse(OrgUnitCreatePayload):
    organization_id: int = Field(default=None, description="ID of the organization this unit belongs to")
    hierarchy: Optional[OrgUnitResponse] = Field(default=[], description="OrgUnitHierarchy of the organization")


# --- from org_unit_value_models.py ---
# Base model for common attributes of an org unit value
class OrgUnitValueBase(BaseModel):
    id: Optional[int] = Field(None, description="Unique identifier of the organization unit value")
    org_unit_id: int = Field(None, description="Unique identifier of the organization unit")
    value: str = Field(..., min_length=1, max_length=255, description="Value of the organization unit")


class OrgUnitValueHierarchy(OrgUnitValueBase):
    children: Optional[List[OrgUnitValueHierarchy]] = Field(default_factory=list, description="Child unit values of the organization unit value")


# Model for organization unit values data received in the request payload for creation
class OrgUnitValueCreatePayload(BaseModel):
    organization_id: int = Field(..., description="ID of the organization these unit values belongs to")
    hierarchy: OrgUnitValueHierarchy = Field(..., description="OrgUnitValueHierarchy of the organization unit values")


# Model for organization unit value data received in the request payload for update
class OrgUnitValueUpdatePayload(OrgUnitValueBase):
    pass


# Model for organization unit values data as stored in the database (includes DB-generated fields)
class OrgUnitValueInDB(OrgUnitValueBase):
    id: int = Field(..., description="Unique identifier of the organization unit value")
    organization_id: Optional[int] = Field(default=None, description="ID of the organization these unit values belongs to")
    org_unit_name: Optional[str] = Field(default=None, description="Organization unit to which this value belongs to")
    parent_org_unit_value_id: Optional[int] = Field(None, description="ID of the parent organization unit value")
    is_active: bool = Field(True, description="Indicates if the organization unit value is active")
    created_on: datetime = Field(..., description="Timestamp when the organization unit value was created")
    created_by: str = Field(..., description="User who created the organization unit value")
    updated_on: Optional[datetime] = Field(None, description="Timestamp when the organization unit value was last updated")
    updated_by: Optional[str] = Field(None, description="User who last updated the organization unit value")

    class Config:
        from_attributes = True


class OrgUnitValueResponse(OrgUnitValueBase):
    org_unit_name: str = Field(..., description="Organization unit to which this value belongs to")
    children: Optional[List[OrgUnitValueResponse]] = Field(default_factory=list, description="Child unit values of the organization unit")
    is_active: bool = Field(True, description="Indicates if the organization unit value is active")
    created_on: datetime = Field(..., description="Timestamp when the organization unit value was created")
    created_by: str = Field(..., description="User who created the organization unit value")
    updated_on: Optional[datetime] = Field(None, description="Timestamp when the organization unit value was last updated")
    updated_by: Optional[str] = Field(None, description="User who last updated the organization unit value")


# Model for API response (can be same as InDB or a subset)
class OrgUnitValuesResponse(OrgUnitValueCreatePayload):
    organization_id: int = Field(..., description="ID of the organization these unit values belong to")
    hierarchy: OrgUnitValueResponse = Field(..., description="OrgUnitValueHierarchy of the organization unit values")


# --- from org_settings_models.py ---
class OrgSettingsBase(BaseModel):
    organization_id: int = Field(..., description="The ID of the organization these settings belong to.")
    retry_count: int = Field(..., ge=0, description="The number of retries allowed for an assessment.")
    is_notification_required: bool = Field(False, description="Indicates if notifications are required for this organization.")

class OrgSettingsCreate(OrgSettingsBase):
    pass

class OrgSettingsUpdate(BaseModel):
    retry_count: Optional[int] = Field(None, ge=0, description="The number of retries allowed for an assessment.")
    is_notification_required: Optional[bool] = Field(None, description="Indicates if notifications are required for this organization.")
    is_active: Optional[bool] = Field(None, description="Activity status of the organization settings.")

class OrgSettingsInDB(OrgSettingsBase):
    id: int = Field(..., description="Unique identifier of the organization settings")
    is_active: bool = Field(True, description="Indicates if the organization settings are active")
    created_on: datetime = Field(..., description="Timestamp when the settings were created")
    created_by: str = Field(..., description="User who created the settings")
    updated_on: Optional[datetime] = Field(None, description="Timestamp when the settings were last updated")
    updated_by: Optional[str] = Field(None, description="User who last updated the settings")

    class Config:
        from_attributes = True

class OrgSettingsResponse(OrgSettingsInDB):
    pass


# --- from org_gate_settings_models.py ---
class OrgGateSettingsBase(BaseModel):
    org_settings_id: int = Field(..., description="The ID of the organization setting these settings belong to.")
    reviewer_count: int = Field(..., gt=0, description="The number of reviewers required for an assessment.")
    approver_count: int = Field(..., gt=0, description="The number of approvers required for an assessment.")
    is_fixed_reviewer: bool = Field(False, description="Indicates if the reviewers are fixed or not.") 
    is_fixed_approver: bool = Field(False, description="Indicates if the approvers are fixed or not.")
    reviewer_emails: List[EmailStr] = Field(default_factory=list, description="Fixed reviewer email IDs. Required when is_fixed_reviewer is true.")
    approver_emails: List[EmailStr] = Field(default_factory=list, description="Fixed approver email IDs. Required when is_fixed_approver is true.")

class OrgGateSettingsCreate(OrgGateSettingsBase):
    pass

class OrgGateSettingsUpdate(BaseModel):
    reviewer_count: Optional[int] = Field(None, gt=0, description="The number of reviewers required for an assessment.")
    approver_count: Optional[int] = Field(None, gt=0, description="The number of approvers required for an assessment.")
    is_fixed_reviewer: Optional[bool] = Field(None, description="Indicates if the reviewers are fixed or not.")
    is_fixed_approver: Optional[bool] = Field(None, description="Indicates if the approvers are fixed or not.")
    reviewer_emails: Optional[List[EmailStr]] = Field(None, description="Fixed reviewer email IDs.")
    approver_emails: Optional[List[EmailStr]] = Field(None, description="Fixed approver email IDs.")
    is_active: Optional[bool] = Field(None, description="Activity status of the organization settings.")

class OrgGateSettingsInDB(OrgGateSettingsBase):
    id: int = Field(..., description="Unique identifier of the organization gate settings")
    is_active: bool = Field(True, description="Indicates if the organization gate settings are active")
    created_on: datetime = Field(..., description="Timestamp when the gate settings were created")
    created_by: str = Field(..., description="User who created the gate settings")
    updated_on: Optional[datetime] = Field(None, description="Timestamp when the gate settings were last updated")
    updated_by: Optional[str] = Field(None, description="User who last updated the gate settings")

    class Config:
        from_attributes = True

class OrgGateSettingsResponse(OrgGateSettingsInDB):
    pass


class OrgAndGateSettingsUpsertRequest(BaseModel):
    retry_count: int = Field(..., ge=0, description="The number of retries allowed for an assessment.")
    is_notification_required: bool = Field(False, description="Indicates if notifications are required for this organization.")
    reviewer_count: int = Field(..., gt=0, description="The number of reviewers required for an assessment.")
    approver_count: int = Field(..., gt=0, description="The number of approvers required for an assessment.")
    is_fixed_reviewer: bool = Field(False, description="Indicates if the reviewers are fixed or not.")
    is_fixed_approver: bool = Field(False, description="Indicates if the approvers are fixed or not.")
    reviewer_emails: List[EmailStr] = Field(default_factory=list, description="Fixed reviewer email IDs. Required when is_fixed_reviewer is true.")
    approver_emails: List[EmailStr] = Field(default_factory=list, description="Fixed approver email IDs. Required when is_fixed_approver is true.")


class OrgAndGateSettingsUpsertResponse(BaseModel):
    org_settings: OrgSettingsResponse
    org_gate_settings: OrgGateSettingsResponse


# --- from org_fixed_actioner_models.py ---
# Base model for common attributes
class OrgActionerBase(BaseModel):
    email: EmailStr = Field(..., description="Email of the reviewer/approver")
    is_reviewer: Optional[bool] = Field(False, decription="Is this a reviewer")
    is_approver: Optional[bool] = Field(False, description="Is this a approver")


class OrgActionerHierarchy(OrgActionerBase):
    next_actioner: Optional[List[OrgActionerHierarchy]] = Field(default_factory=list, description="Object of next actioners")


# Model for creating new actioners (input)
class OrgActionerCreate(BaseModel):
    org_gate_setting_id: int = Field(..., description="ID of the organization gate setting these actioners belong to")
    actioner_hierarchy: Optional[List[OrgActionerHierarchy]] = Field(default_factory=list, description="OrgActionerHierarchy of next actioners")


# Model for updating an existing actioner (input)
class OrgActionerUpdate(OrgActionerBase):
    pass


# Model for actioner data as stored in the database (includes DB-generated fields)
class OrgActionerInDB(OrgActionerBase):
    id: int = Field(..., description="Unique identifier of the actioner")
    org_gate_setting_id: int = Field(..., description="ID of the organization gate setting these actioners belong to")
    is_active: bool = Field(True, description="Indicates if the actioner is active")
    created_on: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Timestamp when the actioner was created")
    created_by: str = Field(default="ADMIN", description="User who created the actioner")
    updated_on: Optional[datetime] = Field(None, description="Timestamp when the actioner was last updated")
    updated_by: Optional[str] = Field(None, description="User who last updated the actioner")

    class Config:
        from_attributes = True


class OrgActionerHierarchyResponse(OrgActionerBase):
    id: int = Field(..., description="Unique identifier of the actioner")
    next_actioner: Optional[List[OrgActionerHierarchyResponse]] = Field(default_factory=list, description="Object of next actioners")
    is_active: bool = Field(True, description="Indicates if the actioner is active")
    created_on: datetime = Field(..., description="Timestamp when the actioner was created")
    created_by: str = Field(..., description="User who created the actioner unit")
    updated_on: Optional[datetime] = Field(None, description="Timestamp when the actioner was last updated")
    updated_by: Optional[str] = Field(None, description="User who last updated the actioner")


# Model for API response (can be same as InDB or a subset)
class OrgGateSettingActionersResponse(OrgActionerCreate):
    org_gate_setting_id: int = Field(..., description="ID of the organization gate setting these actioners belong to")
    actioner_hierarchy: Optional[List[OrgActionerHierarchyResponse]] = Field(..., description="OrgActionerHierarchy of next actioners")


class OrgActionersResponse(BaseModel):
    organization_id: int = Field(..., description="ID of the organization these actioners belong to")
    actioners: List[OrgGateSettingActionersResponse] = Field(..., description="List of all the actioners")


# --- from org_unit_label_mappings_models.py ---
class OrgUnitLabelMappingCreate(BaseModel):
    """Payload to create one or more Org Unit-to-Control links."""
    org_unit_ids: List[int] = Field(..., min_length=1, description="A list of unique org unit IDs to tag to the control.")

class OrgUnitLabelMappingResponse(BaseModel):
    """Response model for a single, successfully created Org Unit-to-Control link."""
    id: int
    org_unit_id: int
    assessment_label_mapping_id: int
    is_active: bool
    created_on: datetime
    created_by: str
    updated_on: Optional[datetime] = None
    updated_by: Optional[str] = None
    class Config:
        from_attributes = True
