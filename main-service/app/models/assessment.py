"""Assessment models.

Merged from: assessment_models, assessment_workflow_models, assessment_label_models, assessment_label_mappings_models, evidence_scope_models.
"""
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field
from pydantic import EmailStr
from pydantic import model_serializer
from typing import List, Optional
from typing import Dict, Any
from app.models.files import AssetLabelMappingResponse
from app.models.files import FileInDB
from app.models.files import PeopleLabelMappingResponse
from app.models.org import OrgUnitLabelMappingResponse


# --- from assessment_models.py ---
class AssessmentStatus(str, Enum):
    PENDING_CREATION_REVIEW = "pending_creation_review"
    CREATION_REVIEW_REJECTED = "creation_review_rejected"
    PENDING_CREATION_APPROVAL = "pending_creation_approval"
    ASSESSMENT_CREATION_REJECTED = "assessment_creation_rejected"
    PENDING_EVIDENCE_MAPPING = "pending_evidence_mapping"
    ASSESSMENT_IN_PROGRESS = "assessment_in_progress"
    PENDING_RUN_REVIEW = "pending_run_review"
    RUN_REVIEW_REJECTED = "run_review_rejected"
    PENDING_FINAL_APPROVAL = "pending_final_approval"
    FINAL_REJECTED = "final_rejected"
    COMPLETED = "completed"

# Base model for common attributes
class AssessmentBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Name of the assessment")
    description: Optional[str] = Field(None, description="Description of the assessment")
    assigned_to: Optional[str] = Field(None, description="User assigned to the assessment")

# Model for creating a new assessment (input)
class AssessmentCreate(AssessmentBase):
    pass

# Model for updating an existing assessment (input)
class AssessmentUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255, description="Name of the assessment")
    description: Optional[str] = Field(None, description="Description of the assessment")
    status: Optional[AssessmentStatus] = Field(None, description="Current status of the assessment")


class AssessmentFrameworkAdd(BaseModel):
    """Add a new framework to an assessment."""
    framework_id: int = Field(..., description="Framework ID to add to the assessment")


class AssessmentFrameworkBulkAdd(BaseModel):
    """Add multiple frameworks to an assessment in a single call."""
    framework_ids: List[int] = Field(
        ...,
        min_items=1,
        description="List of framework IDs to add to the assessment",
    )


class FrameworkReplacement(BaseModel):
    """Single framework replacement item."""
    current_framework_id: int = Field(..., description="Currently mapped framework ID to replace")
    new_framework_id: int = Field(..., description="New framework ID to map")


class AssessmentFrameworkUpdate(BaseModel):
    """Update framework(s) mapped to an assessment. Unified structure for single and bulk replacements."""
    replacements: List[FrameworkReplacement] = Field(
        ...,
        description="List of framework replacements (min 1 item for single, multiple for bulk)",
        min_items=1
    )

    class Config:
        json_schema_extra = {
            "example_single": {
                "replacements": [
                    {
                        "current_framework_id": 7,
                        "new_framework_id": 12
                    }
                ]
            },
            "example_bulk": {
                "replacements": [
                    {
                        "current_framework_id": 7,
                        "new_framework_id": 12
                    },
                    {
                        "current_framework_id": 9,
                        "new_framework_id": 15
                    }
                ]
            }
        }


class ControlReplacement(BaseModel):
    """Single control replacement item."""
    current_control_id: int = Field(..., description="Currently mapped control ID (mapping ID) to replace")
    new_control_node_id: int = Field(..., description="New control node ID to map")


class AssessmentControlUpdate(BaseModel):
    """Update control(s) mapped to an assessment. Supports single and bulk replacements."""
    replacements: List[ControlReplacement] = Field(
        ...,
        description="List of control replacements (min 1 item for single, multiple for bulk)",
        min_items=1
    )

    class Config:
        json_schema_extra = {
            "example_single": {
                "replacements": [
                    {
                        "current_control_id": 42,
                        "new_control_node_id": 105
                    }
                ]
            },
            "example_bulk": {
                "replacements": [
                    {
                        "current_control_id": 42,
                        "new_control_node_id": 105
                    },
                    {
                        "current_control_id": 43,
                        "new_control_node_id": 106
                    }
                ]
            }
        }

# Model for assessment data as stored in the database (includes DB-generated fields)
class AssessmentInDB(AssessmentBase):
    id: int = Field(..., description="Unique identifier of the assessment")
    status: AssessmentStatus = Field(AssessmentStatus.PENDING_CREATION_REVIEW.value, description="Current status of the assessment")
    is_active: bool = Field(True, description="Indicates if the assessment is active")
    created_on: datetime = Field(..., description="Timestamp when the assessment was created")
    created_by: str = Field(..., description="User who created the assessment")
    updated_on: Optional[datetime] = Field(None, description="Timestamp when the assessment was last updated")
    updated_by: Optional[str] = Field(None, description="User who last updated the assessment")
    framework_ids: List[int] = Field(default_factory=list, description="IDs of frameworks mapped to this assessment")

    class Config:
        from_attributes = True

# Model for API response (can be same as InDB or a subset)
class AssessmentResponse(AssessmentInDB):
    pass


class AssessmentListResponse(BaseModel):
    items: List[AssessmentResponse] = Field(default_factory=list, description="Assessments for the requested page")
    page_number: int = Field(..., ge=1, description="Current page number")
    page_size: int = Field(..., ge=1, description="Requested page size")
    total_records: int = Field(..., ge=0, description="Total number of assessments available")
    total_pages: int = Field(..., ge=0, description="Total pages based on page size")


class AssetTagInfo(BaseModel):
    id: int # This is the asset_assessment_label_mapping.id
    asset_id: int

class OrgUnitTagInfo(BaseModel):
    id: int # This is the org_unit_assessment_label_mapping.id
    org_unit_id: int

class PeopleTagInfo(BaseModel):
    id: int # This is the people_assessment_label_mapping.id
    email: str

class ControlTags(BaseModel):
    assets: List[AssetTagInfo] = []
    org_units: List[OrgUnitTagInfo] = []
    people: List[PeopleTagInfo] = []

class EvidenceFileInfo(BaseModel):
    id: int = Field(..., description="Evidence mapping record ID")
    file_id: int = Field(..., description="Evidence file ID")
    name: Optional[str] = Field(None, description="Evidence file name")
    mimetype: Optional[str] = Field(None, description="MIME type")
    file_type: Optional[str] = Field(None, description="File type")
    file_path: Optional[str] = Field(None, description="File path")
    created_on: Optional[datetime] = Field(None, description="Mapping created timestamp")
    created_by: Optional[str] = Field(None, description="Mapping created by")


class ControlWithTagsResponse(BaseModel):
    id: int # This is the assessment_label_mapping.id
    assessment_label_id: int
    node_id: str
    label_id: str | None = Field(None, description="Business label for the control (e.g., 'M1')")
    control_name: str = Field(..., description="Name of the control")
    tags: ControlTags
    evidence_files: List[EvidenceFileInfo] = []

class FrameworkInAssessment(BaseModel):
    id: int # This is the assessment_label.id
    framework_id: int
    framework_name: str = Field(..., description="Name of the framework")
    version_id: int
    controls: List[ControlWithTagsResponse] = []
    files: List[EvidenceFileInfo] = []

class ComprehensiveAssessmentResponse(AssessmentResponse):
    frameworks: List[FrameworkInAssessment] = []


class AssessmentEvidenceFrameworkResponse(BaseModel):
    id: int = Field(..., description="Assessment label ID for the mapped framework")
    framework_id: int = Field(..., description="Framework ID mapped to the assessment")
    framework_name: str = Field(..., description="Framework name")
    version_id: int = Field(..., description="Active framework version mapped to the assessment")
    controls: List[ControlWithTagsResponse] = Field(
        default_factory=list,
        description="Controls under this framework with mapped evidence details",
    )


class AssessmentEvidenceResponse(BaseModel):
    assessment_id: int = Field(..., description="Assessment ID")
    uploaded_evidence_files: List[FileInDB] = Field(
        default_factory=list,
        description="All evidence files uploaded for the assessment",
    )
    frameworks: List[AssessmentEvidenceFrameworkResponse] = Field(
        default_factory=list,
        description="Frameworks with control-level evidence mappings",
    )


class AssessmentRunResponseData(BaseModel):
    # Represents the data returned after running an assessment.
    assessment_version_id: int = Field(..., description="Unique identifier assigned to the assessment run")
    status: str = Field(default=AssessmentStatus.ASSESSMENT_IN_PROGRESS.value, description="Initial status of the assessment")


class AnalysisRunResponse(BaseModel):
    # Represents the response after running an analysis.
    data: AssessmentRunResponseData = Field(..., description="Wrapper for analysis run response")

class RunAssessmentResponse(BaseModel):
    message: str
    assessment_id: int
    new_assessment_version_id: int
    new_version_number: int
    enqueued_files_count: int
    re_run_controls_count: int = 0
    carried_over_controls_count: int = 0


class SubmitAssessmentResponse(BaseModel):
    message: str
    assessment_id: int
    current_status: str
    latest_version_id: Optional[int] = None
    latest_version_number: Optional[int] = None


class AssessmentLLMDetailsResponse(BaseModel):
    """
    Represents assessment details enriched with LLM-run information
    that has been persisted in the database.
    """

    assessment: AssessmentResponse
    control_owner_email: Optional[str] = None
    latest_version_id: Optional[int] = None
    latest_version_number: Optional[int] = None
    frameworks: List[FrameworkInAssessment] = []
    evidence_mappings: List[EvidenceFileInfo] = []
    results_by_label: List[Dict[str, Any]] = []
    detailed_results: List[Dict[str, Any]] = []


class CriteriaResultItem(BaseModel):
    node_id: str
    label_id: Optional[str] = None
    # Populated only for leaf criteria nodes; absent from serialized output for sub_control nodes.
    criteria: Optional[str] = None
    adherence: Optional[float] = None
    reason: Optional[str] = None
    recommendation: Optional[str] = None
    node_type: str = "criteria"
    children: List["CriteriaResultItem"] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def _omit_criteria_for_non_leaf(self, handler) -> dict:
        """Strip the 'criteria' key entirely for non-leaf (sub_control) nodes."""
        d: dict = handler(self)
        if self.node_type != "criteria":
            d.pop("criteria", None)
        return d


class SubControlResultItem(BaseModel):
    node_id: str
    label_id: str
    adherence: Optional[float] = None
    reason: Optional[str] = None
    compliance: Optional[str] = None
    recommendation: Optional[str] = None
    node_type: str = "sub_control"
    children: List[CriteriaResultItem] = []


class AnalysisSplit(BaseModel):
    completed: List[SubControlResultItem] = []
    failed: List[SubControlResultItem] = []
    in_progress: List[SubControlResultItem] = []


class AssessmentResultsV1(BaseModel):
    status: str
    assessment_id: str
    assessment_label_id: str
    analysis: AnalysisSplit


class AssessmentResultsResponse(BaseModel):
    data: Dict[str, AssessmentResultsV1]


# --- from assessment_workflow_models.py ---
class WorkflowActionType(str, Enum):
    """Type of action taken in workflow."""
    APPROVE = "approve"
    REJECT = "reject"


class AssessmentActionerRole(str, Enum):
    """Role in the assessment workflow."""
    REVIEWER = "reviewer"
    APPROVER = "approver"
    CONTROL_OWNER = "control_owner"


class ActionState(str, Enum):
    """State of an action (pending, approved, rejected)."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ReviewerStage(str, Enum):
    """Stages where reviewers act."""
    CREATION_REVIEW = "creation_review"
    RUN_REVIEW = "run_review"


class ApproverStage(str, Enum):
    """Stages where approvers act."""
    CREATION_APPROVAL = "creation_approval"
    FINAL_APPROVAL = "final_approval"


# ========== Assessment Reviewer Table Models (NEW) ==========

class AssessmentReviewerBase(BaseModel):
    """Base model for assessment reviewers (stage-aware)."""
    assessment_id: int = Field(..., description="ID of the assessment")
    email: EmailStr = Field(..., description="Email of the reviewer")
    stage: ReviewerStage = Field(..., description="Workflow stage (creation_review or run_review)")
    action: ActionState = Field(..., description="Current action state (pending/approved/rejected)")
    comments: Optional[str] = Field(None, description="Reviewer comments")


class AssessmentReviewerCreate(AssessmentReviewerBase):
    """Request to create assessment reviewer."""
    pass


class AssessmentReviewerInDB(AssessmentReviewerBase):
    """Assessment reviewer as stored in database."""
    id: int = Field(..., description="Unique identifier")
    is_active: bool = Field(True, description="Active status (soft delete)")
    created_on: datetime = Field(..., description="When created")
    created_by: str = Field(..., description="User who created")
    updated_on: Optional[datetime] = Field(None, description="Last update timestamp")
    updated_by: Optional[str] = Field(None, description="User who updated")

    class Config:
        from_attributes = True


class AssessmentReviewerResponse(AssessmentReviewerInDB):
    """Response model for assessment reviewer."""
    pass


# ========== Assessment Approver Table Models (NEW) ==========

class AssessmentApproverBase(BaseModel):
    """Base model for assessment approvers (stage-aware)."""
    assessment_id: int = Field(..., description="ID of the assessment")
    email: EmailStr = Field(..., description="Email of the approver")
    stage: ApproverStage = Field(..., description="Workflow stage (creation_approval or final_approval)")
    action: ActionState = Field(..., description="Current action state (pending/approved/rejected)")
    comments: Optional[str] = Field(None, description="Approver comments")


class AssessmentApproverCreate(AssessmentApproverBase):
    """Request to create assessment approver."""
    pass


class AssessmentApproverInDB(AssessmentApproverBase):
    """Assessment approver as stored in database."""
    id: int = Field(..., description="Unique identifier")
    is_active: bool = Field(True, description="Active status (soft delete)")
    created_on: datetime = Field(..., description="When created")
    created_by: str = Field(..., description="User who created")
    updated_on: Optional[datetime] = Field(None, description="Last update timestamp")
    updated_by: Optional[str] = Field(None, description="User who updated")

    class Config:
        from_attributes = True


class AssessmentApproverResponse(AssessmentApproverInDB):
    """Response model for assessment approver."""
    pass


# ========== Assessment Control Owner Table Models (NEW) ==========

class AssessmentControlOwnerBase(BaseModel):
    """Base model for assessment control owner."""
    assessment_id: int = Field(..., description="ID of the assessment")
    email: EmailStr = Field(..., description="Email of the control owner")


class AssessmentControlOwnerCreate(AssessmentControlOwnerBase):
    """Request to create assessment control owner."""
    pass


class AssessmentControlOwnerInDB(AssessmentControlOwnerBase):
    """Assessment control owner as stored in database."""
    id: int = Field(..., description="Unique identifier")
    is_active: bool = Field(True, description="Active status (soft delete)")
    created_on: datetime = Field(..., description="When created")
    created_by: str = Field(..., description="User who created")
    updated_on: Optional[datetime] = Field(None, description="Last update timestamp")
    updated_by: Optional[str] = Field(None, description="User who updated")

    class Config:
        from_attributes = True


class AssessmentControlOwnerResponse(AssessmentControlOwnerInDB):
    """Response model for assessment control owner."""
    pass


# ========== Old Assessment Actioner Table Models (DEPRECATED) ==========

class AssessmentActionerBase(BaseModel):
    """Base model for assessment actioners."""
    assessment_id: int = Field(..., description="ID of the assessment")
    email: EmailStr = Field(..., description="Email of the actioner")
    role: AssessmentActionerRole = Field(..., description="Role of the actioner (reviewer/approver/control_owner)")


class AssessmentActionerCreate(AssessmentActionerBase):
    """Request to create assessment actioners."""
    pass


class AssessmentActionerUpdate(BaseModel):
    """Request to update assessment actioner."""
    email: Optional[EmailStr] = Field(None, description="Email of the actioner")
    role: Optional[AssessmentActionerRole] = Field(None, description="Role of the actioner")


class AssessmentActionerInDB(AssessmentActionerBase):
    """Assessment actioner as stored in database."""
    id: int = Field(..., description="Unique identifier")
    is_active: bool = Field(True, description="Active status")
    created_on: datetime = Field(..., description="Creation timestamp")
    created_by: str = Field(..., description="User who created")
    updated_on: Optional[datetime] = Field(None, description="Last update timestamp")
    updated_by: Optional[str] = Field(None, description="User who updated")

    class Config:
        from_attributes = True


class AssessmentActionerResponse(AssessmentActionerInDB):
    """Response model for assessment actioner.

    Optional workflow fields are populated when the actioner row carries
    stage/action info (i.e. reviewers and approvers). For control owners,
    these remain None.
    """
    action: Optional[ActionState] = Field(
        None,
        description="Current action state for this actioner (pending/approved/rejected). None for control owners.",
    )
    stage: Optional[str] = Field(
        None,
        description="Workflow stage this actioner belongs to (creation_review/creation_approval/run_review/final_approval). None for control owners.",
    )
    comments: Optional[str] = Field(
        None,
        description="Most recent comment recorded for this actioner.",
    )
    acted_on: Optional[datetime] = Field(
        None,
        description="Timestamp of the most recent action by this actioner (updated_on if present, else created_on).",
    )


# ========== Assessment Workflow Models ==========
# Maps to existing DB columns:
#   reviewer_id   — legacy FK (kept for backward compat)
#   actioner_id   — new FK to assessment_actioner (nullable during migration)
#   review        — enum('approve','reject')
#   actioned_by   — email of person who acted

class AssessmentWorkflowBase(BaseModel):
    """Base model matching actual assessment_workflow table columns."""
    assessment_id: int = Field(..., description="ID of the assessment")
    actioner_id: Optional[int] = Field(None, description="FK to assessment_actioner")
    reviewer_id: Optional[int] = Field(None, description="Legacy FK to reviewer table (kept for backward compat)")
    review: WorkflowActionType = Field(..., description="Action taken (approve/reject)")
    actioned_by: Optional[str] = Field(None, description="Email of the user who took the action")


class AssessmentWorkflowCreate(AssessmentWorkflowBase):
    """Request to record workflow action."""
    comments: Optional[str] = Field(None, description="Comments from actioner")


class AssessmentWorkflowInDB(AssessmentWorkflowBase):
    """Workflow record as returned from DB."""
    id: int = Field(..., description="Unique identifier")
    comments: Optional[str] = Field(None, description="Comments")
    is_active: bool = Field(True, description="Active status")
    created_on: datetime = Field(..., description="When action was taken")
    created_by: str = Field(..., description="User who created the record")
    updated_on: Optional[datetime] = Field(None, description="Last update timestamp")
    updated_by: Optional[str] = Field(None, description="User who last updated")

    class Config:
        from_attributes = True


class AssessmentWorkflowResponse(AssessmentWorkflowInDB):
    """Response model for workflow."""
    pass


# ========== Workflow Summary Models ==========

class WorkflowStageStatus(BaseModel):
    """Status of a single workflow stage."""
    stage_name: str = Field(..., description="Name of stage (creation_review, creation_approval, evidence_mapping, run_review, final_approval)")
    status: str = Field(..., description="Stage status (pending, approved, rejected, in_progress)")
    actioners: List["WorkflowActionerStatus"] = Field(default=[], description="Actioners in this stage with current action state")
    completed_by: Optional[EmailStr] = Field(None, description="Email of who completed stage")
    action_taken: Optional[WorkflowActionType] = Field(None, description="Action taken in this stage")
    comments: Optional[str] = Field(None, description="Comments for this stage")


class WorkflowActionerStatus(BaseModel):
    """Per-actioner status inside a workflow stage."""
    id: int = Field(..., description="Actioner row id")
    email: EmailStr = Field(..., description="Actioner email")
    role: AssessmentActionerRole = Field(..., description="Actioner role")
    action: ActionState = Field(..., description="Current action state (pending/approved/rejected)")
    stage: str = Field(..., description="Stage key (creation_review/run_review/creation_approval/final_approval/evidence_mapping)")
    comments: Optional[str] = Field(None, description="Most recent comment for the stage")
    acted_on: Optional[datetime] = Field(None, description="Most recent action timestamp")


class AssessmentWorkflowSummary(BaseModel):
    """Complete workflow summary for an assessment."""
    assessment_id: int = Field(..., description="Assessment ID")
    current_status: str = Field(..., description="Current status of assessment")
    version_number: int = Field(..., description="Current version number (max 3)")
    run_count: int = Field(..., description="Number of times assessment run (max 3)")
    rejection_count: int = Field(..., description="Number of rejections/send-backs (max 2)")
    workflow_stages: List[WorkflowStageStatus] = Field(default=[], description="All workflow stages and their status")


# ========== Helper Models ==========

class AssessmentActionersList(BaseModel):
    """List of actioners for an assessment."""
    control_owner: Optional[EmailStr] = Field(None, description="Control owner email")
    reviewers: List[AssessmentActionerResponse] = Field(default=[], description="List of reviewers")
    approvers: List[AssessmentActionerResponse] = Field(default=[], description="List of approvers")


class SelectReviewersAndApproversRequest(BaseModel):
    """Request to select/assign reviewers and approvers for an assessment."""
    assessment_id: int = Field(..., description="Assessment ID")
    control_owner_email: EmailStr = Field(..., description="Email of control owner")
    reviewer_emails: List[EmailStr] = Field(..., min_items=1, description="List of reviewer emails (count must match org settings)")
    approver_emails: List[EmailStr] = Field(..., min_items=1, description="List of approver emails (count must match org settings)")


class ReplaceReviewerRequest(BaseModel):
    """Request to replace one reviewer assignment with another reviewer."""
    assessment_id: int = Field(..., description="Assessment ID")
    current_reviewer_email: EmailStr = Field(..., description="Existing reviewer email to replace")
    new_reviewer_email: EmailStr = Field(..., description="New reviewer email to assign")


class ReplaceApproverRequest(BaseModel):
    """Request to replace one approver assignment with another approver."""
    assessment_id: int = Field(..., description="Assessment ID")
    current_approver_email: EmailStr = Field(..., description="Existing approver email to replace")
    new_approver_email: EmailStr = Field(..., description="New approver email to assign")


class AssessmentApprovalRequest(BaseModel):
    """Request for reviewer/approver to approve or reject assessment."""
    assessment_id: int = Field(..., description="Assessment ID")
    action: WorkflowActionType = Field(..., description="Action (approve/reject)")
    comments: Optional[str] = Field(None, description="Comments")


class AssessmentDeploymentResponse(BaseModel):
    """Response after assessment approval/deployment."""
    success: bool = Field(..., description="Whether deployment was successful")
    message: str = Field(..., description="Details about deployment")
    assessment_id: int = Field(..., description="Assessment ID")
    current_status: str = Field(..., description="New assessment status")


# --- from assessment_label_models.py ---
class AssessmentLabelBase(BaseModel):
    assessment_id: int = Field(..., description="ID of the assessment the label is to be attached to")
    framework_id: int = Field(..., description="ID of the framework to be attached")

class AssessmentLabelCreate(AssessmentLabelBase):
    pass

class AssessmentLabelInDB(AssessmentLabelBase):
    id: int = Field(..., description="Unique identifier for the assessment label")
    version_id: int = Field(..., description="ID of the active version of the framework")
    is_active: bool
    created_on: datetime = Field(..., description="Timestamp when the assessment label was created")
    created_by: str = Field(..., description="User who created the assessment label")
    updated_on: Optional[datetime] = Field(None, description="Timestamp when the assessment label was last updated")
    updated_by: Optional[str] = Field(None, description="User who last updated the assessment label")

class AssessmentLabelResponse(AssessmentLabelInDB):
    pass

class AssessmentLabelRead(AssessmentLabelInDB):
    """
    Represents the detailed view of an assessment label, including
    the framework's name, description, and its specific version number.
    """
    framework_name: str = Field(..., description="Name of the associated framework")
    framework_description: Optional[str] = Field(None, description="Description of the associated framework")
    version_number: int = Field(..., description="Version number of the framework linked to this label")


# --- from assessment_label_mappings_models.py ---
class LabelMappingInDB(BaseModel):
    id:int
    assessment_label_id: int
    node_id: str
    is_active: bool
    created_on: datetime
    created_by: str
    updated_on: Optional[datetime]
    updated_by: Optional[str]

class LabelMappingCreate(BaseModel):
    assessment_label_id: int
    node_id: str
    created_by: str


class LabelMappingReplaceRequest(BaseModel):
    node_id: str = Field(..., description="Replacement control node ID for the mapping")


class BulkLabelMappingCreatePayload(BaseModel):
    assessment_label_id: int = Field(..., description="Assessment label ID to map controls to")
    node_ids: List[str] = Field(..., min_items=1, description="List of control node IDs to map")


class BulkLabelMappingReplacePayload(BaseModel):
    replacements: List[dict] = Field(
        ...,
        description="List of {label_mapping_id, node_id} pairs to update",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "replacements": [
                    {"label_mapping_id": 1, "node_id": "NEW_NODE_1"},
                    {"label_mapping_id": 2, "node_id": "NEW_NODE_2"},
                ]
            }
        }


class ControlTaggingResponse(BaseModel):
    """Aggregated view of a control mapping and all of its tagged org units, people and assets.

    Used to re-hydrate the Org Unit / People / Asset dropdowns when viewing an
    existing assessment control row.
    """
    label_mapping: LabelMappingInDB
    org_units: List[OrgUnitLabelMappingResponse] = Field(default_factory=list)
    people: List[PeopleLabelMappingResponse] = Field(default_factory=list)
    assets: List[AssetLabelMappingResponse] = Field(default_factory=list)


# --- from evidence_scope_models.py ---
# ---------------------------------------------------------------------
# Per-entity response models (ASSESSMENT-level tagging)
# ---------------------------------------------------------------------
class AssetAssessmentMappingResponse(BaseModel):
    """A single Asset-to-Assessment evidence-scope link."""
    id: int
    asset_id: int
    assessment_id: int
    is_active: bool
    created_on: datetime
    created_by: str
    updated_on: Optional[datetime] = None
    updated_by: Optional[str] = None

    class Config:
        from_attributes = True


class OrgUnitAssessmentMappingResponse(BaseModel):
    """A single Org Unit-to-Assessment evidence-scope link."""
    id: int
    org_unit_id: int
    assessment_id: int
    is_active: bool
    created_on: datetime
    created_by: str
    updated_on: Optional[datetime] = None
    updated_by: Optional[str] = None

    class Config:
        from_attributes = True


class PeopleAssessmentMappingResponse(BaseModel):
    """A single Person-to-Assessment evidence-scope link."""
    id: int
    email: EmailStr
    assessment_id: int
    is_active: bool
    created_on: datetime
    created_by: str
    updated_on: Optional[datetime] = None
    updated_by: Optional[str] = None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------
# Combined Evidence Scope request / response (single Save & Continue API)
# ---------------------------------------------------------------------
class EvidenceScopeUpdate(BaseModel):
    """Payload for the consolidated "Evidence Scope" step.

    A single call replaces the org-unit, people and asset tags for an
    assessment. `Software` and `Hardware` are both asset types, so their
    selected values are merged into a single `asset_ids` list.

    Replace semantics per category:
      - field omitted / ``null`` -> that category is left UNCHANGED.
      - field present (even ``[]``) -> that category is REPLACED with the
        provided values (an empty list clears all tags in that category).

    This makes the same endpoint usable both for the initial save and for
    re-submitting different values when the user navigates back.
    """
    org_unit_ids: Optional[List[int]] = Field(
        default=None,
        description="Organization unit IDs to tag to the assessment. Null = leave unchanged.",
    )
    emails: Optional[List[EmailStr]] = Field(
        default=None,
        description="People (user emails) to tag to the assessment. Null = leave unchanged.",
    )
    asset_ids: Optional[List[int]] = Field(
        default=None,
        description="Asset IDs (Software + Hardware combined) to tag to the assessment. Null = leave unchanged.",
    )


class EvidenceScopeResponse(BaseModel):
    """Aggregated evidence scope currently tagged to an assessment.

    Used both as the response of the Save & Continue call and to re-hydrate
    the Organization Unit / People / Software / Hardware dropdowns when the
    user revisits the screen.
    """
    assessment_id: int
    org_units: List[OrgUnitAssessmentMappingResponse] = Field(default_factory=list)
    people: List[PeopleAssessmentMappingResponse] = Field(default_factory=list)
    assets: List[AssetAssessmentMappingResponse] = Field(default_factory=list)
