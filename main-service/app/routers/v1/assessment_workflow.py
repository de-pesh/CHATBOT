"""
Assessment workflow router.
Endpoints for reviewer/approver assignment, actions, and status tracking.
"""

from fastapi import APIRouter, Depends, status

from app.dependencies import get_db_service, CurrentUserDep, has_permission
from app.models.assessment import (
    AssessmentActionersList,
    SelectReviewersAndApproversRequest,
    ReplaceReviewerRequest,
    ReplaceApproverRequest,
    AssessmentApprovalRequest,
    AssessmentDeploymentResponse,
    AssessmentWorkflowSummary,
)
from app.services.database_service import DatabaseService
from app.core.permissions import Permission
from app.controllers.assessment_workflow_controller import AssessmentWorkflowController

router = APIRouter()


@router.post(
    "/{assessment_id}/assign-reviewers-approvers",
    response_model=AssessmentActionersList,
    status_code=status.HTTP_200_OK,
    summary="Assign reviewers and approvers to an assessment",
    description="Assign control owner, reviewers, and approvers to an assessment based on organization settings.",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_UPDATE]))]
)
async def assign_reviewers_and_approvers(
    assessment_id: int,
    request: SelectReviewersAndApproversRequest,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> AssessmentActionersList:
    """
    Assign reviewers and approvers to an assessment.
    This typically happens after control mapping, before running the assessment.
    """
    controller = AssessmentWorkflowController(db_service)
    return await controller.assign_reviewers_and_approvers(
        assessment_id, request, current_user.email
    )


@router.get(
    "/{assessment_id}/actioners",
    response_model=AssessmentActionersList,
    status_code=status.HTTP_200_OK,
    summary="Get assigned actioners for an assessment",
    description="Retrieve all assigned control owner, reviewers, and approvers for an assessment.",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_READ]))]
)
async def get_assessment_actioners(
    assessment_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> AssessmentActionersList:
    """Get all assigned actioners for an assessment."""
    controller = AssessmentWorkflowController(db_service)
    return await controller.get_assessment_actioners(assessment_id)


@router.patch(
    "/{assessment_id}/reviewers/replace",
    response_model=AssessmentActionersList,
    status_code=status.HTTP_200_OK,
    summary="Replace one reviewer assignment",
    description="Replace one reviewer with another reviewer for an assessment.",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_UPDATE]))]
)
async def replace_reviewer(
    assessment_id: int,
    request: ReplaceReviewerRequest,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> AssessmentActionersList:
    """Replace one reviewer at a time for an assessment."""
    controller = AssessmentWorkflowController(db_service)
    return await controller.replace_reviewer(assessment_id, request, current_user.email)


@router.patch(
    "/{assessment_id}/approvers/replace",
    response_model=AssessmentActionersList,
    status_code=status.HTTP_200_OK,
    summary="Replace one approver assignment",
    description="Replace one approver with another approver for an assessment.",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_UPDATE]))]
)
async def replace_approver(
    assessment_id: int,
    request: ReplaceApproverRequest,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> AssessmentActionersList:
    """Replace one approver at a time for an assessment."""
    controller = AssessmentWorkflowController(db_service)
    return await controller.replace_approver(assessment_id, request, current_user.email)


@router.post(
    "/{assessment_id}/approve-or-reject",
    response_model=AssessmentDeploymentResponse,
    status_code=status.HTTP_200_OK,
    summary="Approve or reject assessment at any workflow stage",
    description="Reviewer/approver takes action on assessment (approve or reject).",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_READ]))]
)
async def approve_or_reject_assessment(
    assessment_id: int,
    request: AssessmentApprovalRequest,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> AssessmentDeploymentResponse:
    """
    Reviewer/approver takes action on assessment.
    - approve: Move to next stage or complete if final approver
    - reject: Mark stage rejected with comments for remediation
    """
    controller = AssessmentWorkflowController(db_service)
    return await controller.approve_or_reject_assessment(
        assessment_id, request, current_user.email
    )


@router.get(
    "/{assessment_id}/workflow-status",
    response_model=AssessmentWorkflowSummary,
    status_code=status.HTTP_200_OK,
    summary="Get complete workflow status for an assessment",
    description="Retrieve workflow summary including current stage, actioners, and history.",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_READ]))]
)
async def get_workflow_status(
    assessment_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> AssessmentWorkflowSummary:
    """Get complete workflow status and history for an assessment."""
    controller = AssessmentWorkflowController(db_service)
    return await controller.get_workflow_status(assessment_id)


@router.get(
    "/{assessment_id}/next-action-required",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="Check what action is required next in workflow",
    description="Determine next action needed and who should perform it.",
    dependencies=[Depends(has_permission([Permission.ASSESSMENT_READ]))]
)
async def get_next_action_required(
    assessment_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> dict:
    """
    Get next action required in the workflow.
    Returns info about:
    - Current status
    - Who needs to take action next
    - What action is needed
    - Deadline/priority if applicable
    """
    controller = AssessmentWorkflowController(db_service)
    return await controller.get_next_action_required(assessment_id)
