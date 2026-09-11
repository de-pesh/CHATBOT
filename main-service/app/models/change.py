"""Change models.

Merged from: change, change_request_models.
"""
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field
from pydantic import model_validator
from typing import Any, Optional, List
from uuid import UUID


# --- from change.py ---
# --- Enums ---


class ApprovalStatus(str, Enum):
    OPEN = "open"
    APPROVED = "approved"
    REJECTED = "rejected"


class ChangeActionTypeEnum(str, Enum):
    ADD = "add"
    EDIT = "edit"
    DELETE = "delete"
    MOVE = "move"


# --- Node Data Model (for request body) ---
class NodeData(BaseModel):
    node_id: Optional[UUID] = Field(None, description="ID of the node being changed (for existing nodes)")
    label_id: str = Field(..., description="Hierarchical label/path of the node")
    name: str = Field(..., description="Name of the node")
    description: Optional[str] = Field(None, description="Description of the node")
    label_type: int = Field(..., description="ID of Type of the item")
    parent_node_id: Optional[UUID] = Field(None, description="ID of the parent node")


# --- API Input Model for adding a change ---
class ChangeCreatePayload(BaseModel):
    framework_id: int = Field(..., description="Framework this change is being pushed to")
    version_id: Optional[int] = Field(None, description="Target version ID. If omitted, backend resolves the best-fit active version.")
    action_type: ChangeActionTypeEnum = Field(..., description="Type of change (add, edit, delete, move)")
    file_id: Optional[int] = Field(None, description="File ID from which this change belongs to. Optional for edit/delete/move where the node already exists.")
    node_data: NodeData = Field(..., description="Data of the node after the change")
    remarks: Optional[str] = Field(None, description="Remarks about the change")

    @model_validator(mode='before')
    def validate_node_id(cls, data: Any) -> Any:
        action = data.get('action_type')
        node_data = data.get('node_data')

        if not node_data:
            return data # Let other validators handle the missing field

        # node_data may be a dict (from JSON) or a NodeData model instance (from programmatic construction)
        if isinstance(node_data, dict):
            node_id = node_data.get('node_id')
        else:
            node_id = getattr(node_data, 'node_id', None)

        # Normalize action_type: may be enum value string or enum instance
        action_str = action.value if hasattr(action, 'value') else action

        if action_str in [ChangeActionTypeEnum.EDIT.value, ChangeActionTypeEnum.DELETE.value, ChangeActionTypeEnum.MOVE.value] and not node_id:
            raise ValueError(f"node_id is required for '{action_str}' action.")

        if action_str == ChangeActionTypeEnum.ADD.value and node_id:
            raise ValueError("node_id should not be provided for 'add' action.")
            
        return data


# --- Core Change Models ---
class ChangeBase(BaseModel):
    change_request_id: int = Field(..., description="ID of the change request this change belongs to")
    status: ApprovalStatus = Field(..., description="Approval status of the change")
    label_id: str = Field(..., description="Hierarchical label/path of the node")
    action_type: ChangeActionTypeEnum = Field(..., description="Type of change (add, edit, delete, move)")
    remarks: Optional[str] = Field(None, description="Remarks about the change")


class ChangeCreate(ChangeBase):
    pass


class ChangeUpdate(BaseModel):
    status: Optional[ApprovalStatus] = Field(None, description="Approval status of the change")
    remarks: Optional[str] = Field(None, description="Remarks about the change (e.g. reason for rejection)")


class ChangeInDB(ChangeBase):
    id: int = Field(..., description="Unique identifier of the change")
    is_active: bool = Field(..., description="Indicates if the change is active")
    created_on: datetime = Field(..., description="Timestamp when the change was created")
    created_by: str = Field(..., description="User who created the change")
    updated_on: Optional[datetime] = Field(None, description="Timestamp when the change was last updated")
    updated_by: Optional[str] = Field(None, description="User who last updated the change")

    class Config:
        from_attributes = True


class ChangeResponse(ChangeInDB):
    pass


class NodeDataResponse(BaseModel):
    node_id: Optional[UUID] = Field(None, description="ID of the node being changed (for existing nodes)")
    label_id: Optional[str] = Field(None, description="Hierarchical label/path of the node")
    name: Optional[str] = Field(None, description="Name of the node")
    description: Optional[str] = Field(None, description="Description of the node")
    label_type: Optional[int] = Field(None, description="ID of Type of the item")
    parent_node_id: Optional[UUID] = Field(None, description="ID of the parent node")


class ChangeDetailResponse(BaseModel):
    old: NodeDataResponse = Field(..., description="Old details of the node")
    new: NodeDataResponse = Field(..., description="New details of the node")


class NodeDetailsInDB(BaseModel):
    node_id: str = Field(..., description="Unique Identifier of the Framework Node")
    label_id: str = Field(..., description="Unique Label of the Framework Node")
    name: str = Field(..., description="Name of the Framework Node")
    description: Optional[str] = Field(None, description="Description of the Framework Node")
    guidelines: Optional[str] = Field(None, description="Guidelines of the Framework Node")
    correspoding_question: Optional[str] = Field(None, description="Corresponding Question of the Framework Node")
    label_type: int = Field(..., description="Label type ID of the Framework Node")
    created_on: datetime = Field(..., description="Date when the framework node was created")
    created_by: str = Field(..., description="Person who created the framework node")
    updated_on: Optional[datetime] = Field(None, description="Date when the framework node was updated")
    updated_by: Optional[str] = Field(None, description="Person who updated the framework node")


# --- Bulk Import Models ---
class HierarchyNode(BaseModel):
    """Represents a single node in the hierarchy tree."""
    label_id: str = Field(..., description="Hierarchical label (e.g., 'M', 'M1', 'M1.1')")
    name: str = Field(..., description="Name of the node")
    description: Optional[str] = Field(None, description="Description of the node")
    label_type: Optional[int] = Field(None, description="Label type ID (defaults to inferring from depth)")
    children: Optional[List['HierarchyNode']] = Field(default_factory=list, description="Child nodes")


HierarchyNode.model_rebuild()


class BulkImportPayload(BaseModel):
    """Payload for bulk importing a framework hierarchy."""
    framework_id: int = Field(..., description="Framework ID to import into")
    version_id: Optional[int] = Field(None, description="Target version ID (optional, backend will select latest active)")
    file_id: Optional[int] = Field(None, description="File ID for the import (optional)")
    hierarchy: HierarchyNode = Field(..., description="Root node of the hierarchy tree")
    auto_approve: bool = Field(default=True, description="Automatically approve all created nodes")
    remarks: Optional[str] = Field(None, description="Remarks for all changes in bulk import")


class BulkImportNodeResult(BaseModel):
    """Result of importing a single node."""
    label_id: str = Field(..., description="Label ID of the node")
    name: str = Field(..., description="Name of the node")
    node_id: Optional[UUID] = Field(None, description="Created node ID")
    status: str = Field(..., description="Status: 'created', 'error', 'skipped'")
    message: Optional[str] = Field(None, description="Status message or error details")


class BulkImportResponse(BaseModel):
    """Response from bulk import operation."""
    framework_id: int = Field(..., description="Framework ID")
    version_id: int = Field(..., description="Version ID used for import")
    total_nodes_requested: int = Field(..., description="Total nodes in input hierarchy")
    nodes_created: int = Field(..., description="Number of nodes successfully created")
    nodes_failed: int = Field(..., description="Number of nodes that failed")
    nodes_skipped: int = Field(..., description="Number of nodes that were skipped")
    duration_seconds: float = Field(..., description="Total duration of import in seconds")
    node_results: List[BulkImportNodeResult] = Field(..., description="Detailed results for each node")


class BulkImportByDepthPayload(BaseModel):
    """
    Payload for bulk importing one depth level at a time from the regulation JSON.
    
    The JSON format expected in `nodes` is:
    {
        "M1": { "name": "...", "childs": { "M1.1": { ... } } },
        "M2": { "name": "...", "childs": { ... } },
        ...
    }
    
    Call this endpoint once per depth level:
    - depth=1: creates M1, M2, ..., T9   (parent_node_id = null)
    - depth=2: creates M1.1, M1.2, ...   (parent resolved from DB)
    - depth=3: creates M1.1.1, M1.1.2, ...
    - depth=4: creates M1.1.1.1, M1.1.1.2, ...
    """
    framework_id: int = Field(..., description="Framework ID to import into")
    version_id: Optional[int] = Field(None, description="Target version ID (optional)")
    file_id: Optional[int] = Field(None, description="File ID for the import")
    change_request_id: Optional[int] = Field(None, description="Existing open change request ID to add changes into. If omitted, the backend auto-resolves the latest open CR for the version.")
    depth: int = Field(..., ge=1, le=10, description="Depth level to import (1=root nodes, 2=children, 3=grandchildren, etc.)")
    label_type: int = Field(..., description="label_type ID for nodes at this depth (from framework configuration)")
    nodes: Any = Field(..., description="The full regulation JSON with label_ids as keys and childs as nested dict")
    remarks: Optional[str] = Field(None, description="Remarks for all created changes")


# --- from change_request_models.py ---
class ChangeRequestInDB(BaseModel):
    id: int = Field(..., description="Unique identifier of the change request")
    version_id: int = Field(..., description="Unique identifier of the version")
    status: ApprovalStatus = Field(..., description="Approval status of the change request")
    is_active: bool = Field(..., description="Indicates if the change request is active")
    created_on: datetime = Field(..., description="Timestamp when the change request was created")
    created_by: str = Field(..., description="User who created the change request")
    updated_on: Optional[datetime] = Field(None, description="Timestamp when the change request was last updated")
    updated_by: Optional[str] = Field(None, description="User who last updated the change request")


class ChangeRequestResponse(ChangeRequestInDB):
    pass
