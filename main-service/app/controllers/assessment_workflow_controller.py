"""
Assessment workflow controller.
Handles reviewer/approver assignment, approval/rejection actions, and workflow orchestration.
"""
import logging
from typing import Dict, Any, List, Optional, Set
from fastapi import HTTPException, status

from app.core.stored_procedures import StoredProcedures
from app.models.assessment import AssessmentStatus
from app.models.assessment import (
    AssessmentActionerResponse,
    AssessmentActionersList,
    SelectReviewersAndApproversRequest,
    ReplaceReviewerRequest,
    ReplaceApproverRequest,
    AssessmentApprovalRequest,
    AssessmentDeploymentResponse,
    AssessmentWorkflowSummary,
    WorkflowStageStatus,
    WorkflowActionerStatus,
    WorkflowActionType,
    AssessmentActionerRole,
    ActionState,
    ReviewerStage,
    ApproverStage,
)
from app.services.database_service import DatabaseService
from app.services.workflow_service import AssessmentWorkflowService

logger = logging.getLogger(__name__)


class AssessmentWorkflowController:
    """Controller for assessment workflow operations."""

    def __init__(self, db_service: DatabaseService):
        self.db_service = db_service
        self.workflow_service = AssessmentWorkflowService(db_service)

    @staticmethod
    def _to_bool(value: Any) -> bool:
        """Normalize DB truthy values (e.g. 0/1, '0'/'1', 'true'/'false') into bool."""
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        if isinstance(value, (int, float)):
            return value != 0

        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "t", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "f", "no", "n", "off", ""}:
            return False

        return bool(value)

    @staticmethod
    def _to_generic_actioner_response(actioner: Any, role: AssessmentActionerRole) -> AssessmentActionerResponse:
        """Convert reviewer/approver/control-owner row models to generic actioner response shape.

        Carries the per-actioner workflow state (stage, action, comments, acted_on)
        when available on the source row so callers of GET /actioners can see
        whether each reviewer/approver has approved, rejected, or is still pending.
        """
        raw_stage = getattr(actioner, "stage", None)
        if raw_stage is not None:
            stage_value = raw_stage.value if hasattr(raw_stage, "value") else str(raw_stage)
        else:
            stage_value = None

        action_value = getattr(actioner, "action", None)
        comments_value = getattr(actioner, "comments", None)
        updated_on = getattr(actioner, "updated_on", None)
        created_on = getattr(actioner, "created_on", None)
        acted_on = updated_on or created_on

        return AssessmentActionerResponse(
            id=actioner.id,
            assessment_id=actioner.assessment_id,
            email=actioner.email,
            role=role,
            is_active=actioner.is_active,
            created_on=actioner.created_on,
            created_by=actioner.created_by,
            updated_on=actioner.updated_on,
            updated_by=actioner.updated_by,
            action=action_value,
            stage=stage_value,
            comments=comments_value,
            acted_on=acted_on,
        )

    @staticmethod
    def _latest_rows_by_email_and_stage(rows: List[Any]) -> Dict[tuple, Any]:
        """Keep only latest row by (email, stage) using max id as recency proxy."""
        latest: Dict[tuple, Any] = {}
        for row in rows:
            email = str(getattr(row, "email", "") or "").strip().lower()
            stage = str(getattr(row, "stage", "") or "").strip().lower()
            if not email or not stage:
                continue
            key = (email, stage)
            current = latest.get(key)
            if current is None or int(row.id) > int(current.id):
                latest[key] = row
        return latest

    @staticmethod
    def _stage_lifecycle_status(current_status: str, stage_key: str) -> str:
        """Map assessment current_status to stage lifecycle status."""
        post_creation_review = {
            AssessmentStatus.PENDING_CREATION_APPROVAL.value,
            AssessmentStatus.ASSESSMENT_CREATION_REJECTED.value,
            AssessmentStatus.PENDING_EVIDENCE_MAPPING.value,
            AssessmentStatus.ASSESSMENT_IN_PROGRESS.value,
            AssessmentStatus.PENDING_RUN_REVIEW.value,
            AssessmentStatus.RUN_REVIEW_REJECTED.value,
            AssessmentStatus.PENDING_FINAL_APPROVAL.value,
            AssessmentStatus.FINAL_REJECTED.value,
            AssessmentStatus.COMPLETED.value,
        }
        post_creation_approval = {
            AssessmentStatus.PENDING_EVIDENCE_MAPPING.value,
            AssessmentStatus.ASSESSMENT_IN_PROGRESS.value,
            AssessmentStatus.PENDING_RUN_REVIEW.value,
            AssessmentStatus.RUN_REVIEW_REJECTED.value,
            AssessmentStatus.PENDING_FINAL_APPROVAL.value,
            AssessmentStatus.FINAL_REJECTED.value,
            AssessmentStatus.COMPLETED.value,
        }
        post_run_review = {
            AssessmentStatus.PENDING_FINAL_APPROVAL.value,
            AssessmentStatus.FINAL_REJECTED.value,
            AssessmentStatus.COMPLETED.value,
        }

        if stage_key == ReviewerStage.CREATION_REVIEW.value:
            if current_status == AssessmentStatus.PENDING_CREATION_REVIEW.value:
                return "in_progress"
            if current_status == AssessmentStatus.CREATION_REVIEW_REJECTED.value:
                return "rejected"
            if current_status in post_creation_review:
                return "completed"
            return "pending"

        if stage_key == ApproverStage.CREATION_APPROVAL.value:
            if current_status == AssessmentStatus.PENDING_CREATION_APPROVAL.value:
                return "in_progress"
            if current_status == AssessmentStatus.ASSESSMENT_CREATION_REJECTED.value:
                return "rejected"
            if current_status in post_creation_approval:
                return "completed"
            return "pending"

        if stage_key == "evidence_mapping":
            if current_status in {
                AssessmentStatus.PENDING_EVIDENCE_MAPPING.value,
                AssessmentStatus.RUN_REVIEW_REJECTED.value,
                AssessmentStatus.FINAL_REJECTED.value,
            }:
                return "in_progress"
            if current_status in {
                AssessmentStatus.ASSESSMENT_IN_PROGRESS.value,
                AssessmentStatus.PENDING_RUN_REVIEW.value,
                AssessmentStatus.PENDING_FINAL_APPROVAL.value,
                AssessmentStatus.COMPLETED.value,
            }:
                return "completed"
            return "pending"

        if stage_key == ReviewerStage.RUN_REVIEW.value:
            if current_status == AssessmentStatus.PENDING_RUN_REVIEW.value:
                return "in_progress"
            if current_status == AssessmentStatus.RUN_REVIEW_REJECTED.value:
                return "rejected"
            if current_status in post_run_review:
                return "completed"
            return "pending"

        if stage_key == ApproverStage.FINAL_APPROVAL.value:
            if current_status == AssessmentStatus.PENDING_FINAL_APPROVAL.value:
                return "in_progress"
            if current_status == AssessmentStatus.FINAL_REJECTED.value:
                return "rejected"
            if current_status == AssessmentStatus.COMPLETED.value:
                return "completed"
            return "pending"

        return "pending"

    async def _validate_actioner_replace_context(
        self,
        assessment_id: int,
        new_email: str,
        role: AssessmentActionerRole,
    ) -> None:
        assessment_result = await self.db_service.call_procedure(
            StoredProcedures.FILTER_ASSESSMENTS,
            [assessment_id, None, None, None, True, 1, 1],
        )
        if not assessment_result or not assessment_result[0]:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Assessment not found.",
            )

        assessment_row = assessment_result[0][0]
        current_status = str(assessment_row.get("status") or "").strip().lower()
        creator_email = str(assessment_row.get("created_by") or "").strip().lower()

        allowed_statuses = {
            AssessmentStatus.PENDING_CREATION_REVIEW.value,
            AssessmentStatus.CREATION_REVIEW_REJECTED.value,
            AssessmentStatus.PENDING_CREATION_APPROVAL.value,
            AssessmentStatus.ASSESSMENT_CREATION_REJECTED.value,
            AssessmentStatus.PENDING_EVIDENCE_MAPPING.value,
        }
        if current_status not in allowed_statuses:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Actioner updates are not allowed in status '{current_status}'. "
                    "Allowed statuses: pending_creation_review, creation_review_rejected, "
                    "pending_creation_approval, assessment_creation_rejected, pending_evidence_mapping."
                ),
            )

        if new_email == creator_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Assessment creator cannot be assigned as reviewer or approver.",
            )

        organization_id = await self._resolve_org_id_from_assessment(assessment_id)
        gate_config = await self._get_org_gate_config(organization_id)
        is_fixed_reviewer = self._to_bool(gate_config.get("is_fixed_reviewer"))
        is_fixed_approver = self._to_bool(gate_config.get("is_fixed_approver"))

        if role == AssessmentActionerRole.REVIEWER and is_fixed_reviewer:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reviewer updates are disabled because fixed reviewers are enabled in organization settings.",
            )
        if role == AssessmentActionerRole.APPROVER and is_fixed_approver:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Approver updates are disabled because fixed approvers are enabled in organization settings.",
            )

        org_user_emails = await self._get_org_user_emails(organization_id)
        if new_email not in org_user_emails:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Replacement user must belong to the same organization as the assessment creator.",
            )

    async def _resolve_org_id_from_assessment(self, assessment_id: int) -> int:
        assessment_result = await self.db_service.call_procedure(
            StoredProcedures.FILTER_ASSESSMENTS,
            [assessment_id, None, None, None, True, 1, 1],
        )
        if not assessment_result or not assessment_result[0]:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Assessment not found.",
            )

        created_by_email = assessment_result[0][0].get("created_by")
        if not created_by_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Assessment creator email is missing; cannot resolve organization workflow settings.",
            )

        users_result = await self.db_service.call_procedure(
            StoredProcedures.FILTER_USERS,
            [None, None, None, created_by_email, None, True, 1, 1],
        )
        if not users_result or not users_result[0] or not users_result[0][0]:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Assessment creator user record not found.",
            )

        org_id = users_result[0][0].get("organization_id")
        if org_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Assessment creator is not mapped to any organization.",
            )
        return int(org_id)

    async def _get_org_gate_config(self, organization_id: int) -> Dict[str, Any]:
        org_settings = await self.db_service.call_procedure(
            StoredProcedures.FILTER_ORG_SETTINGS,
            [None, organization_id, True, 1, 1000],
        )
        if not org_settings or not org_settings[0]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Organization settings not found for organization_id={organization_id}.",
            )

        # Select the latest active org setting record in case older active rows exist.
        latest_org_setting = max(
            org_settings[0],
            key=lambda row: (row.get("updated_on") or row.get("created_on") or "", row.get("id") or 0),
        )
        org_settings_id = latest_org_setting.get("id")
        gate_settings = await self.db_service.call_procedure(
            StoredProcedures.FILTER_ORG_GATE_SETTINGS,
            [None, org_settings_id, None, True, 1, 1000],
        )
        if not gate_settings or not gate_settings[0]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Organization gate settings not found for org_settings_id={org_settings_id}.",
            )

        latest_gate_setting = max(
            gate_settings[0],
            key=lambda row: (row.get("updated_on") or row.get("created_on") or "", row.get("id") or 0),
        )

        return latest_gate_setting

    async def _get_org_user_emails(self, organization_id: int) -> Set[str]:
        users_result = await self.db_service.call_procedure(
            StoredProcedures.FILTER_USERS,
            [None, None, None, None, organization_id, True, 1, 100000],
        )
        if not users_result or not users_result[0]:
            return set()
        return {
            str(u.get("email")).strip().lower()
            for u in users_result[0]
            if u.get("email")
        }

    async def _get_fixed_actioner_emails(self, org_settings_id: int) -> Dict[str, Set[str]]:
        fixed_actioners = await self.db_service.call_procedure(
            StoredProcedures.FILTER_ORG_ACTIONER,
            [None, None, org_settings_id, None, None, True, 1, 1000],
        )
        reviewer_emails: Set[str] = set()
        approver_emails: Set[str] = set()

        if fixed_actioners and fixed_actioners[0]:
            for row in fixed_actioners[0]:
                email = str(row.get("email") or "").strip().lower()
                if not email:
                    continue
                if row.get("is_reviewer"):
                    reviewer_emails.add(email)
                if row.get("is_approver"):
                    approver_emails.add(email)

        return {
            "reviewers": reviewer_emails,
            "approvers": approver_emails,
        }

    async def assign_reviewers_and_approvers(
        self,
        assessment_id: int,
        request: SelectReviewersAndApproversRequest,
        user_id: str,
    ) -> AssessmentActionersList:
        """
        Assign control owner, reviewers, and approvers to an assessment.
        Validates count against org settings for the assessment's organization.
        """
        try:
            if request.assessment_id != assessment_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Path assessment_id does not match request.assessment_id.",
                )

            assessment_result = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENTS,
                [assessment_id, None, None, None, True, 1, 1],
            )
            if not assessment_result or not assessment_result[0]:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Assessment not found.",
                )
            creator_email = str(assessment_result[0][0].get("created_by") or "").strip().lower()

            organization_id = await self._resolve_org_id_from_assessment(assessment_id)
            gate_config = await self._get_org_gate_config(organization_id)
            org_gate_setting_id = gate_config.get("id")
            org_settings_id = gate_config.get("org_setting_id") or gate_config.get("org_settings_id")
            reviewer_count_required = int(gate_config.get("reviewer_count") or 0)
            approver_count_required = int(gate_config.get("approver_count") or 0)
            is_fixed_reviewer = self._to_bool(gate_config.get("is_fixed_reviewer"))
            is_fixed_approver = self._to_bool(gate_config.get("is_fixed_approver"))

            if org_settings_id is None:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Unable to resolve org_settings_id from org gate settings.",
                )

            reviewer_emails = [str(e).strip().lower() for e in request.reviewer_emails]
            approver_emails = [str(e).strip().lower() for e in request.approver_emails]
            control_owner_email = str(request.control_owner_email).strip().lower()

            if len(set(reviewer_emails)) != len(reviewer_emails):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Duplicate reviewer emails are not allowed.",
                )
            if len(set(approver_emails)) != len(approver_emails):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Duplicate approver emails are not allowed.",
                )

            if creator_email and creator_email in set(reviewer_emails):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Assessment creator cannot be assigned as reviewer.",
                )
            if creator_email and creator_email in set(approver_emails):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Assessment creator cannot be assigned as approver.",
                )

            if len(reviewer_emails) != reviewer_count_required:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Reviewer count mismatch. Expected exactly {reviewer_count_required} "
                        f"reviewer(s) as per organization setup, got {len(reviewer_emails)}."
                    ),
                )
            if len(approver_emails) != approver_count_required:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Approver count mismatch. Expected exactly {approver_count_required} "
                        f"approver(s) as per organization setup, got {len(approver_emails)}."
                    ),
                )

            org_user_emails = await self._get_org_user_emails(organization_id)
            if control_owner_email not in org_user_emails:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Control owner must be a user from the same organization.",
                )

            if is_fixed_reviewer or is_fixed_approver:
                fixed_emails = await self._get_fixed_actioner_emails(int(org_settings_id))
            else:
                fixed_emails = {"reviewers": set(), "approvers": set()}

            if is_fixed_reviewer:
                configured_fixed_reviewers = fixed_emails["reviewers"]
                if not configured_fixed_reviewers:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Fixed reviewers are enabled in organization setup but no fixed reviewers are configured.",
                    )
                if set(reviewer_emails) != configured_fixed_reviewers:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            "Reviewer selection must exactly match configured fixed reviewers "
                            f"for org_settings_id={org_settings_id}."
                        ),
                    )
            else:
                outside_org_reviewers = [e for e in reviewer_emails if e not in org_user_emails]
                if outside_org_reviewers:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Reviewer(s) not in organization: {outside_org_reviewers}",
                    )

            if is_fixed_approver:
                configured_fixed_approvers = fixed_emails["approvers"]
                if not configured_fixed_approvers:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Fixed approvers are enabled in organization setup but no fixed approvers are configured.",
                    )
                if set(approver_emails) != configured_fixed_approvers:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            "Approver selection must exactly match configured fixed approvers "
                            f"for org_settings_id={org_settings_id}."
                        ),
                    )
            else:
                outside_org_approvers = [e for e in approver_emails if e not in org_user_emails]
                if outside_org_approvers:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Approver(s) not in organization: {outside_org_approvers}",
                    )

            # 3. Validate minimum rule still holds if org config is inconsistent
            if len(reviewer_emails) < 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="At least one reviewer email is required."
                )
            if len(approver_emails) < 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="At least one approver email is required."
                )

            # 4. Assign all actioners
            await self.workflow_service.assign_actioners_to_assessment(
                assessment_id,
                control_owner_email,
                reviewer_emails,
                approver_emails,
                user_id
            )

            # 5. Transition assessment to reviewer-driven creation review
            await self.workflow_service.transition_to_creation_review(assessment_id, user_id)

            # 6. Return full actioner records from DB (not id-only upsert outputs)
            return await self.get_assessment_actioners(assessment_id)

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error assigning reviewers/approvers to assessment {assessment_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to assign reviewers and approvers."
            )

    async def get_assessment_actioners(
        self, assessment_id: int
    ) -> AssessmentActionersList:
        """Retrieve assigned actioners for an assessment."""
        try:
            actioners_by_role = await self.workflow_service.get_assessment_actioners(assessment_id)
            
            control_owner_email = None
            if actioners_by_role["control_owner"]:
                control_owner_email = actioners_by_role["control_owner"][0].email

            has_any = (
                control_owner_email
                or actioners_by_role["reviewers"]
                or actioners_by_role["approvers"]
            )
            if not has_any:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No actioners assigned to this assessment."
                )

            reviewer_responses = [
                self._to_generic_actioner_response(r, AssessmentActionerRole.REVIEWER)
                for r in actioners_by_role["reviewers"]
            ]
            approver_responses = [
                self._to_generic_actioner_response(a, AssessmentActionerRole.APPROVER)
                for a in actioners_by_role["approvers"]
            ]

            return AssessmentActionersList(
                control_owner=control_owner_email,
                reviewers=reviewer_responses,
                approvers=approver_responses
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error retrieving actioners for assessment {assessment_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve assessment actioners."
            )

    async def replace_reviewer(
        self,
        assessment_id: int,
        request: ReplaceReviewerRequest,
        user_id: str,
    ) -> AssessmentActionersList:
        """Replace one reviewer with another reviewer (one-by-one update)."""
        try:
            if request.assessment_id != assessment_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Path assessment_id does not match request.assessment_id.",
                )

            current_email = str(request.current_reviewer_email).strip().lower()
            new_email = str(request.new_reviewer_email).strip().lower()
            if current_email == new_email:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Current reviewer and new reviewer cannot be the same.",
                )

            await self._validate_actioner_replace_context(
                assessment_id=assessment_id,
                new_email=new_email,
                role=AssessmentActionerRole.REVIEWER,
            )

            current_rows_res = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENT_REVIEWERS,
                [None, assessment_id, current_email, None, None, True, 1, 1000],
            )
            current_rows = current_rows_res[0] if current_rows_res and current_rows_res[0] else []
            if not current_rows:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Reviewer '{current_email}' is not assigned to this assessment.",
                )

            new_rows_res = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENT_REVIEWERS,
                [None, assessment_id, new_email, None, None, True, 1, 1000],
            )
            if new_rows_res and new_rows_res[0]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Reviewer '{new_email}' is already assigned to this assessment.",
                )

            for row in current_rows:
                await self.db_service.call_procedure(
                    StoredProcedures.DELETE_ASSESSMENT_REVIEWER,
                    [row["id"], user_id],
                )

            seen_stages = set()
            for row in current_rows:
                stage = str(row.get("stage") or "").strip().lower()
                if not stage or stage in seen_stages:
                    continue
                seen_stages.add(stage)
                await self.db_service.call_procedure(
                    StoredProcedures.UPSERT_ASSESSMENT_REVIEWER,
                    [None, assessment_id, new_email, stage, ActionState.PENDING.value, None, True, user_id],
                )

            return await self.get_assessment_actioners(assessment_id)

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error replacing reviewer for assessment {assessment_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to replace reviewer.",
            )

    async def replace_approver(
        self,
        assessment_id: int,
        request: ReplaceApproverRequest,
        user_id: str,
    ) -> AssessmentActionersList:
        """Replace one approver with another approver (one-by-one update)."""
        try:
            if request.assessment_id != assessment_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Path assessment_id does not match request.assessment_id.",
                )

            current_email = str(request.current_approver_email).strip().lower()
            new_email = str(request.new_approver_email).strip().lower()
            if current_email == new_email:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Current approver and new approver cannot be the same.",
                )

            await self._validate_actioner_replace_context(
                assessment_id=assessment_id,
                new_email=new_email,
                role=AssessmentActionerRole.APPROVER,
            )

            current_rows_res = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENT_APPROVERS,
                [None, assessment_id, current_email, None, None, True, 1, 1000],
            )
            current_rows = current_rows_res[0] if current_rows_res and current_rows_res[0] else []
            if not current_rows:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Approver '{current_email}' is not assigned to this assessment.",
                )

            new_rows_res = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENT_APPROVERS,
                [None, assessment_id, new_email, None, None, True, 1, 1000],
            )
            if new_rows_res and new_rows_res[0]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Approver '{new_email}' is already assigned to this assessment.",
                )

            for row in current_rows:
                await self.db_service.call_procedure(
                    StoredProcedures.DELETE_ASSESSMENT_APPROVER,
                    [row["id"], user_id],
                )

            seen_stages = set()
            for row in current_rows:
                stage = str(row.get("stage") or "").strip().lower()
                if not stage or stage in seen_stages:
                    continue
                seen_stages.add(stage)
                await self.db_service.call_procedure(
                    StoredProcedures.UPSERT_ASSESSMENT_APPROVER,
                    [None, assessment_id, new_email, stage, ActionState.PENDING.value, None, True, user_id],
                )

            return await self.get_assessment_actioners(assessment_id)

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error replacing approver for assessment {assessment_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to replace approver.",
            )

    async def approve_or_reject_assessment(
        self,
        assessment_id: int,
        request: AssessmentApprovalRequest,
        user_id: str,
    ) -> AssessmentDeploymentResponse:
        """
        Handle reviewer/approver action on assessment.
        Uses new stage-aware reviewer/approver tables.
        
        State machine:
        - pending_creation_review (Reviewers: creation_review stage) → all approve → pending_creation_approval (Approvers: creation_approval)
        - pending_creation_approval (Approvers: creation_approval) → approve → pending_evidence_mapping (CO) or reject → back to assessor
        - pending_evidence_mapping (CO) → run → pending_run_review (Reviewers: run_review stage)
        - pending_run_review (reviewers run_review stage in any order) → all approve → pending_final_approval (Approvers: final_approval)
        - pending_final_approval (approvers final_approval) → approve → completed
        
        On reject:
        - creation_review/creation_approval → back to compliance assessor
        - run_review/final_approval → back to control owner
        - second final approval rejection → non-compliant remediation outcome
        """
        try:
            # 1. Get current assessment
            assessment_result = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENTS,
                [assessment_id, None, None, None, True, 1, 1]
            )
            if not assessment_result or not assessment_result[0]:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Assessment not found."
                )
            assessment = assessment_result[0][0]
            current_status = assessment.get("status")
            creator_email = str(assessment.get("created_by") or "").strip().lower()

            # 2. Determine which stage we're in and validate user is an assigned actioner
            # The user_id is the email address of the authenticated user
            actioner_email = str(user_id).strip().lower()

            if creator_email and actioner_email == creator_email:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Assessment creator cannot act as reviewer or approver.",
                )

            stage: Optional[ReviewerStage | ApproverStage] = None
            is_reviewer = False
            is_approver = False

            if current_status == AssessmentStatus.PENDING_CREATION_REVIEW.value:
                stage = ReviewerStage.CREATION_REVIEW
                is_reviewer = True
            elif current_status == AssessmentStatus.PENDING_CREATION_APPROVAL.value:
                stage = ApproverStage.CREATION_APPROVAL
                is_approver = True
            elif current_status == AssessmentStatus.PENDING_RUN_REVIEW.value:
                stage = ReviewerStage.RUN_REVIEW
                is_reviewer = True
            elif current_status == AssessmentStatus.PENDING_FINAL_APPROVAL.value:
                stage = ApproverStage.FINAL_APPROVAL
                is_approver = True
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Assessment is in '{current_status}' status. No workflow action can be taken."
                )

            # 3. Validate user is an assigned actioner for this stage
            if is_reviewer:
                rev_result = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_ASSESSMENT_REVIEWERS,
                    [None, assessment_id, actioner_email, stage.value, None, True, 1, 10]
                )
                if not rev_result or not rev_result[0]:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"User {actioner_email} is not an assigned reviewer for {stage.value} stage."
                    )
            elif is_approver:
                app_result = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_ASSESSMENT_APPROVERS,
                    [None, assessment_id, actioner_email, stage.value, None, True, 1, 10]
                )
                if not app_result or not app_result[0]:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"User {actioner_email} is not an assigned approver for {stage.value} stage."
                    )

            # 4. Convert WorkflowActionType to ActionState
            if request.action == WorkflowActionType.APPROVE:
                action_state = ActionState.APPROVED
            elif request.action == WorkflowActionType.REJECT:
                action_state = ActionState.REJECTED
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unknown action: {request.action}"
                )

            # 5. Record the action in appropriate table (reviewer or approver)
            if is_reviewer:
                await self.workflow_service.record_reviewer_action(
                    assessment_id=assessment_id,
                    reviewer_email=actioner_email,
                    stage=stage,
                    action=action_state,
                    comments=request.comments,
                    user_id=user_id,
                )
            elif is_approver:
                await self.workflow_service.record_approver_action(
                    assessment_id=assessment_id,
                    approver_email=actioner_email,
                    stage=stage,
                    action=action_state,
                    comments=request.comments,
                    user_id=user_id,
                )

            # 6. Handle approval/rejection logic based on current status and action
            new_status = current_status
            response_message = f"Assessment {request.action.value} successfully."
            action_taken = False

            if request.action == WorkflowActionType.APPROVE:
                # ========== APPROVAL PATH ==========
                
                if current_status == AssessmentStatus.PENDING_CREATION_REVIEW.value:
                    # Check if all creation_review reviewers have approved
                    all_reviewers_approved = await self.workflow_service.have_all_actioners_approved(
                        assessment_id,
                        AssessmentActionerRole.REVIEWER,
                        ReviewerStage.CREATION_REVIEW.value,
                    )
                    if not all_reviewers_approved:
                        action_taken = True
                        response_message = "Approval recorded. Waiting for other reviewers."
                    else:
                        new_status = await self.workflow_service.transition_to_creation_approval(
                            assessment_id, user_id
                        )
                        action_taken = True
                        response_message = "All reviewers approved. Assessment moved to creation approval stage."

                elif current_status == AssessmentStatus.PENDING_CREATION_APPROVAL.value:
                    # Approver approving controls → move to evidence_mapping (CO)
                    new_status = await self.workflow_service.transition_to_evidence_mapping(
                        assessment_id, user_id
                    )
                    action_taken = True
                    response_message = "Creation approval approved. Assessment moved to evidence mapping stage."

                elif current_status == AssessmentStatus.PENDING_RUN_REVIEW.value:
                    # Check if all run_review reviewers have approved
                    all_reviewers_approved = await self.workflow_service.have_all_actioners_approved(
                        assessment_id,
                        AssessmentActionerRole.REVIEWER,
                        ReviewerStage.RUN_REVIEW.value,
                    )
                    if not all_reviewers_approved:
                        action_taken = True
                        response_message = "Approval recorded. Waiting for other reviewers."
                    else:
                        # All reviewers approved → move to FINAL_APPROVAL
                        new_status = await self.workflow_service.transition_to_final_approval(
                            assessment_id, user_id
                        )
                        action_taken = True
                        response_message = "All reviewers approved. Assessment moved to final approval stage."

                elif current_status == AssessmentStatus.PENDING_FINAL_APPROVAL.value:
                    # Check if all final_approval approvers have approved
                    all_approvers_approved = await self.workflow_service.have_all_actioners_approved(
                        assessment_id,
                        AssessmentActionerRole.APPROVER,
                        ApproverStage.FINAL_APPROVAL.value,
                    )
                    if not all_approvers_approved:
                        action_taken = True
                        response_message = "Approval recorded. Waiting for other approvers."
                    else:
                        new_status = await self.workflow_service.transition_to_completed(
                            assessment_id, user_id
                        )
                        action_taken = True
                        response_message = "All approvers approved. Assessment marked completed."

            elif request.action == WorkflowActionType.REJECT:
                # ========== REJECTION PATH ==========
                
                if current_status == AssessmentStatus.PENDING_CREATION_REVIEW.value:
                    # Reviewer rejected → back to compliance assessor
                    new_status = await self.workflow_service.transition_to_rejected(
                        assessment_id, AssessmentStatus.CREATION_REVIEW_REJECTED.value, user_id
                    )
                    action_taken = True
                    response_message = "Creation review rejected. Returned to compliance assessor for control updates."

                elif current_status == AssessmentStatus.PENDING_CREATION_APPROVAL.value:
                    # Approver rejected → back to compliance assessor
                    new_status = await self.workflow_service.transition_to_rejected(
                        assessment_id, AssessmentStatus.ASSESSMENT_CREATION_REJECTED.value, user_id
                    )
                    action_taken = True
                    response_message = "Creation approval rejected. Returned to compliance assessor."

                elif current_status == AssessmentStatus.PENDING_RUN_REVIEW.value:
                    # Reviewer rejected → back to evidence_mapping (CO re-maps evidence)
                    new_status = await self.workflow_service.transition_to_rejected(
                        assessment_id, AssessmentStatus.RUN_REVIEW_REJECTED.value, user_id
                    )
                    action_taken = True
                    response_message = "Run review rejected. Returned to control owner for evidence updates and rerun."

                elif current_status == AssessmentStatus.PENDING_FINAL_APPROVAL.value:
                    # Approver rejected
                    final_reject_count = await self.workflow_service.get_final_approval_rejection_count(assessment_id)
                    new_status = await self.workflow_service.transition_to_rejected(
                        assessment_id, AssessmentStatus.FINAL_REJECTED.value, user_id
                    )
                    action_taken = True
                    if final_reject_count >= 1:
                        response_message = "Final approval rejected again. Assessment marked non-compliant and sent for remediation."
                    else:
                        response_message = "Final approval rejected. Returned to control owner for one re-upload and rerun cycle."

            # 7. Return response
            if not action_taken:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Cannot {request.action.value} assessment in status '{current_status}'."
                )

            return AssessmentDeploymentResponse(
                success=True,
                message=response_message,
                assessment_id=assessment_id,
                current_status=new_status
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error processing approval/rejection for assessment {assessment_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process approval/rejection."
            )

    async def get_workflow_status(
        self, assessment_id: int
    ) -> AssessmentWorkflowSummary:
        """Get complete workflow status including all stages and history."""
        try:
            # 1. Get assessment
            assessment_result = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENTS,
                [assessment_id, None, None, None, True, 1, 1]
            )
            if not assessment_result or not assessment_result[0]:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Assessment not found."
                )
            assessment = assessment_result[0][0]

            # 2. Get version count
            version_count = await self.workflow_service.get_assessment_run_count(assessment_id)

            # 3. Get rejection count
            rejection_count = await self.workflow_service.get_assessment_rejection_count(assessment_id)

            # 4. Get actioners by role
            actioners_by_role = await self.workflow_service.get_assessment_actioners(assessment_id)

            # 5. Get workflow history (reserved for future use)
            # workflow_history = await self.workflow_service.get_workflow_history(assessment_id)

            # 6. Build stage statuses with per-actioner latest action
            current_status = assessment.get("status", "")
            all_reviewers = actioners_by_role.get("reviewers", [])
            all_approvers = actioners_by_role.get("approvers", [])

            def stage_actioners_for_reviewers(stage_key: str) -> List[WorkflowActionerStatus]:
                rows = [
                    row for row in all_reviewers
                       if (getattr(row, "stage", None) and
                           (row.stage.value if hasattr(row.stage, "value") else str(row.stage)).strip().lower() == stage_key.lower())
                ]
                rows.sort(key=lambda r: (str(r.email).lower(), r.id))
                return [
                    WorkflowActionerStatus(
                        id=row.id,
                        email=row.email,
                        role=AssessmentActionerRole.REVIEWER,
                        action=row.action,
                        stage=stage_key,
                        comments=row.comments,
                        acted_on=row.updated_on or row.created_on,
                    )
                    for row in rows
                ]

            def stage_actioners_for_approvers(stage_key: str) -> List[WorkflowActionerStatus]:
                rows = [
                    row for row in all_approvers
                       if (getattr(row, "stage", None) and
                           (row.stage.value if hasattr(row.stage, "value") else str(row.stage)).strip().lower() == stage_key.lower())
                ]
                rows.sort(key=lambda r: (str(r.email).lower(), r.id))
                return [
                    WorkflowActionerStatus(
                        id=row.id,
                        email=row.email,
                        role=AssessmentActionerRole.APPROVER,
                        action=row.action,
                        stage=stage_key,
                        comments=row.comments,
                        acted_on=row.updated_on or row.created_on,
                    )
                    for row in rows
                ]

            evidence_actioners: List[WorkflowActionerStatus] = []
            for co in (actioners_by_role.get("control_owner") or []):
                evidence_actioners.append(
                    WorkflowActionerStatus(
                        id=co.id,
                        email=co.email,
                        role=AssessmentActionerRole.CONTROL_OWNER,
                        action=ActionState.PENDING,
                        stage="evidence_mapping",
                        comments=None,
                        acted_on=co.updated_on or co.created_on,
                    )
                )

            stages = [
                WorkflowStageStatus(
                    stage_name="creation_review",
                    status=self._stage_lifecycle_status(current_status, ReviewerStage.CREATION_REVIEW.value),
                    actioners=stage_actioners_for_reviewers(ReviewerStage.CREATION_REVIEW.value),
                    completed_by=None,
                    action_taken=None,
                    comments=None,
                ),
                WorkflowStageStatus(
                    stage_name="creation_approval",
                    status=self._stage_lifecycle_status(current_status, ApproverStage.CREATION_APPROVAL.value),
                    actioners=stage_actioners_for_approvers(ApproverStage.CREATION_APPROVAL.value),
                    completed_by=None,
                    action_taken=None,
                    comments=None,
                ),
                WorkflowStageStatus(
                    stage_name="evidence_mapping",
                    status=self._stage_lifecycle_status(current_status, "evidence_mapping"),
                    actioners=evidence_actioners,
                    completed_by=None,
                    action_taken=None,
                    comments=None,
                ),
                WorkflowStageStatus(
                    stage_name="run_review",
                    status=self._stage_lifecycle_status(current_status, ReviewerStage.RUN_REVIEW.value),
                    actioners=stage_actioners_for_reviewers(ReviewerStage.RUN_REVIEW.value),
                    completed_by=None,
                    action_taken=None,
                    comments=None,
                ),
                WorkflowStageStatus(
                    stage_name="final_approval",
                    status=self._stage_lifecycle_status(current_status, ApproverStage.FINAL_APPROVAL.value),
                    actioners=stage_actioners_for_approvers(ApproverStage.FINAL_APPROVAL.value),
                    completed_by=None,
                    action_taken=None,
                    comments=None,
                ),
            ]

            # 7. Return summary
            return AssessmentWorkflowSummary(
                assessment_id=assessment_id,
                current_status=current_status,
                version_number=version_count,
                run_count=version_count,
                rejection_count=rejection_count,
                workflow_stages=stages
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error getting workflow status for assessment {assessment_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve workflow status."
            )

    async def get_next_action_required(self, assessment_id: int) -> Dict[str, Any]:
        """Determine what action is required next and who should take it."""
        try:
            # 1. Get current assessment
            assessment_result = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENTS,
                [assessment_id, None, None, None, True, 1, 1]
            )
            if not assessment_result or not assessment_result[0]:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Assessment not found."
                )
            assessment = assessment_result[0][0]
            current_status = assessment.get("status", "")

            # 2. Get actioners
            actioners_by_role = await self.workflow_service.get_assessment_actioners(assessment_id)

            # 3. Determine next action based on status
            next_actions = {
                AssessmentStatus.PENDING_CREATION_REVIEW.value: {
                    "action": "review_control_mapping",
                    "responsible_role": "reviewer",
                    "next_actors": [r.email for r in actioners_by_role["reviewers"][:1]],
                    "description": "Reviewers to review and approve mapped controls"
                },
                AssessmentStatus.PENDING_CREATION_APPROVAL.value: {
                    "action": "approve_control_mapping",
                    "responsible_role": "approver",
                    "next_actors": [a.email for a in actioners_by_role["approvers"][:1]],
                    "description": "Compliance manager/approver to approve control mapping"
                },
                AssessmentStatus.PENDING_EVIDENCE_MAPPING.value: {
                    "action": "map_evidence",
                    "responsible_role": "control_owner",
                    "next_actors": [actioners_by_role["control_owner"][0].email] if actioners_by_role["control_owner"] else [],
                    "description": "Control Owner to upload and map evidence files"
                },
                AssessmentStatus.ASSESSMENT_IN_PROGRESS.value: {
                    "action": "wait_for_completion",
                    "responsible_role": "system",
                    "next_actors": [],
                    "description": "Assessment is running - waiting for completion"
                },
                AssessmentStatus.PENDING_RUN_REVIEW.value: {
                    "action": "review_assessment_run",
                    "responsible_role": "reviewer",
                    "next_actors": [r.email for r in actioners_by_role["reviewers"][:1]],
                    "description": "Reviewers to review assessment results"
                },
                AssessmentStatus.PENDING_FINAL_APPROVAL.value: {
                    "action": "approve_assessment",
                    "responsible_role": "approver",
                    "next_actors": [a.email for a in actioners_by_role["approvers"][:1]],
                    "description": "Compliance manager/approver to approve final assessment decision"
                },
                AssessmentStatus.FINAL_REJECTED.value: {
                    "action": "rerun_or_remediate",
                    "responsible_role": "control_owner",
                    "next_actors": [actioners_by_role["control_owner"][0].email] if actioners_by_role["control_owner"] else [],
                    "description": "Control owner may re-upload/re-run once; repeated rejection leads to non-compliant remediation"
                },
                AssessmentStatus.COMPLETED.value: {
                    "action": "none",
                    "responsible_role": "none",
                    "next_actors": [],
                    "description": "Assessment workflow completed"
                },
            }

            action_info = next_actions.get(current_status, {
                "action": "unknown",
                "responsible_role": "unknown",
                "next_actors": [],
                "description": f"Workflow status: {current_status}"
            })

            return {
                "assessment_id": assessment_id,
                "current_status": current_status,
                **action_info
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error determining next action for assessment {assessment_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to determine next action."
            )
