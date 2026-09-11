"""
Workflow service for managing assessment status transitions and workflow logic.
Implements the state machine: creation → evidence mapping → run → review → approval → completed.
"""
import logging
from enum import Enum
from typing import Optional, Dict, Any, List, Tuple
from fastapi import HTTPException, status

from app.core.stored_procedures import StoredProcedures
from app.models.assessment import AssessmentStatus
from app.models.assessment import (
    WorkflowActionType,
    AssessmentActionerRole,
    ActionState,
    ReviewerStage,
    ApproverStage,
    AssessmentReviewerInDB,
    AssessmentApproverInDB,
    AssessmentControlOwnerInDB,
    AssessmentWorkflowInDB,
)
from app.services.database_service import DatabaseService

logger = logging.getLogger(__name__)


class WorkflowStage(str, Enum):
    """Workflow stages in assessment lifecycle."""
    CREATION_REVIEW = "pending_creation_review"
    CREATION_APPROVAL = "pending_creation_approval"
    EVIDENCE_MAPPING = "pending_evidence_mapping"
    RUN_REVIEW = "pending_run_review"
    FINAL_APPROVAL = "pending_final_approval"
    COMPLETED = "completed"


class AssessmentWorkflowService:
    """
    Manages assessment workflow state transitions and actioner coordination.
    
    Key constraints:
    - Run attempts hard-capped to 3 total versions
    - Reviewer approvals can happen in any order; all must approve before approver stage
    - Sequential approver chain → all must approve before completion
    - On rejection: revert to control owner for evidence re-mapping
    """

    def __init__(self, db_service: DatabaseService):
        self.db_service = db_service

    @staticmethod
    def _extract_upsert_id(result: Any) -> Optional[int]:
        """Extract inserted/updated id from upsert procedure result payload."""
        try:
            if not result or not result[0] or "id" not in result[0][0]:
                return None
            return int(result[0][0]["id"])
        except Exception:
            return None

    async def _fetch_control_owner_by_id(self, owner_id: int) -> AssessmentControlOwnerInDB:
        fetched = await self.db_service.call_procedure(
            StoredProcedures.FILTER_ASSESSMENT_CONTROL_OWNERS,
            [owner_id, None, None, True, 1, 1],
        )
        if not fetched or not fetched[0]:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Control owner saved but could not be fetched.",
            )
        return AssessmentControlOwnerInDB(**fetched[0][0])

    async def _fetch_reviewer_by_id(self, reviewer_id: int) -> AssessmentReviewerInDB:
        fetched = await self.db_service.call_procedure(
            StoredProcedures.FILTER_ASSESSMENT_REVIEWERS,
            [reviewer_id, None, None, None, None, True, 1, 1],
        )
        if not fetched or not fetched[0]:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Reviewer saved but could not be fetched.",
            )
        return AssessmentReviewerInDB(**fetched[0][0])

    async def _fetch_approver_by_id(self, approver_id: int) -> AssessmentApproverInDB:
        fetched = await self.db_service.call_procedure(
            StoredProcedures.FILTER_ASSESSMENT_APPROVERS,
            [approver_id, None, None, None, None, True, 1, 1],
        )
        if not fetched or not fetched[0]:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Approver saved but could not be fetched.",
            )
        return AssessmentApproverInDB(**fetched[0][0])

    async def _find_existing_reviewer_row_id(
        self,
        assessment_id: int,
        email: str,
        stage: str,
    ) -> Optional[int]:
        """Return the latest active reviewer row id for (assessment_id, email, stage), if any."""
        try:
            result = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENT_REVIEWERS,
                [None, assessment_id, email, stage, None, True, 1, 1000],
            )
            rows = result[0] if result and result[0] else []
            if not rows:
                return None
            # Pick the most recently created row (highest id) as the canonical one to update
            latest = max(rows, key=lambda r: int(r.get("id") or 0))
            return int(latest.get("id")) if latest.get("id") is not None else None
        except Exception:
            # Fall back to insert path if lookup fails
            logger.exception(
                f"Lookup of existing reviewer row failed for assessment={assessment_id}, "
                f"email={email}, stage={stage}; will insert a new row."
            )
            return None

    async def _find_existing_approver_row_id(
        self,
        assessment_id: int,
        email: str,
        stage: str,
    ) -> Optional[int]:
        """Return the latest active approver row id for (assessment_id, email, stage), if any."""
        try:
            result = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENT_APPROVERS,
                [None, assessment_id, email, stage, None, True, 1, 1000],
            )
            rows = result[0] if result and result[0] else []
            if not rows:
                return None
            latest = max(rows, key=lambda r: int(r.get("id") or 0))
            return int(latest.get("id")) if latest.get("id") is not None else None
        except Exception:
            logger.exception(
                f"Lookup of existing approver row failed for assessment={assessment_id}, "
                f"email={email}, stage={stage}; will insert a new row."
            )
            return None

    @staticmethod
    def _db_review_value(action: WorkflowActionType) -> str:
        """
        Normalize review value for DB compatibility.

        Live DB currently accepts approve/reject enum literals.
        """
        if action == WorkflowActionType.APPROVE:
            return "approve"
        return "reject"

    @staticmethod
    def _normalize_review_from_db(value: Any) -> WorkflowActionType:
        """Map DB review literals to API enum values used by the service."""
        normalized = str(value or "").strip().lower()
        if normalized in {"approve", "approved"}:
            return WorkflowActionType.APPROVE
        if normalized in {"reject", "rejected"}:
            return WorkflowActionType.REJECT
        return WorkflowActionType.REJECT

    # ========== Core Workflow Transitions ==========

    async def transition_to_creation_review(
        self, assessment_id: int, user_id: str
    ) -> str:
        """Assessment created → pending creation review from compliance manager."""
        try:
            await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ASSESSMENT,
                [
                    assessment_id, None, None,
                    AssessmentStatus.PENDING_CREATION_REVIEW.value,
                    None, None, user_id
                ]
            )
            return AssessmentStatus.PENDING_CREATION_REVIEW.value
        except Exception as e:
            logger.exception(f"Failed to transition assessment {assessment_id} to creation_review: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to transition assessment status."
            )

    async def transition_to_creation_approval(
        self, assessment_id: int, user_id: str
    ) -> str:
        """All reviewers approved controls → pending creation approval from compliance manager."""
        try:
            await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ASSESSMENT,
                [
                    assessment_id, None, None,
                    AssessmentStatus.PENDING_CREATION_APPROVAL.value,
                    None, None, user_id
                ]
            )
            return AssessmentStatus.PENDING_CREATION_APPROVAL.value
        except Exception as e:
            logger.exception(f"Failed to transition assessment {assessment_id} to creation_approval: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to transition assessment status."
            )

    async def transition_to_evidence_mapping(
        self, assessment_id: int, user_id: str
    ) -> str:
        """Compliance manager approved controls → control owner maps evidence."""
        try:
            await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ASSESSMENT,
                [
                    assessment_id, None, None,
                    AssessmentStatus.PENDING_EVIDENCE_MAPPING.value,
                    None, None, user_id
                ]
            )
            return AssessmentStatus.PENDING_EVIDENCE_MAPPING.value
        except Exception as e:
            logger.exception(f"Failed to transition assessment {assessment_id} to evidence_mapping: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to transition assessment status."
            )

    async def transition_to_run_in_progress(
        self, assessment_id: int, user_id: str
    ) -> str:
        """Evidence mapped → assessment run in progress."""
        try:
            await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ASSESSMENT,
                [
                    assessment_id, None, None,
                    AssessmentStatus.ASSESSMENT_IN_PROGRESS.value,
                    None, None, user_id
                ]
            )
            return AssessmentStatus.ASSESSMENT_IN_PROGRESS.value
        except Exception as e:
            logger.exception(f"Failed to transition assessment {assessment_id} to in_progress: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to transition assessment status."
            )

    async def transition_to_run_review(
        self, assessment_id: int, user_id: str
    ) -> str:
        """
        Assessment run complete → pending review from reviewers for run_review stage.
        Creates new reviewer rows with stage='run_review' and action='pending' for existing reviewers.
        """
        try:
            # Get baseline reviewers from creation_review stage only.
            reviewers_result = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENT_REVIEWERS,
                [None, assessment_id, None, ReviewerStage.CREATION_REVIEW.value, None, True, 1, 1000],
            )
            reviewer_emails = sorted(
                {
                    str(row.get("email")).strip().lower()
                    for row in (reviewers_result[0] if reviewers_result and reviewers_result[0] else [])
                    if row.get("email")
                }
            )
            
            # Create new reviewer rows for run_review stage with action=pending
            if reviewer_emails:
                await self.assign_reviewers_for_stage(
                    assessment_id=assessment_id,
                    stage=ReviewerStage.RUN_REVIEW,
                    reviewer_emails=reviewer_emails,
                    user_id=user_id,
                )
            
            # Transition assessment status
            await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ASSESSMENT,
                [
                    assessment_id, None, None,
                    AssessmentStatus.PENDING_RUN_REVIEW.value,
                    None, None, user_id
                ]
            )
            return AssessmentStatus.PENDING_RUN_REVIEW.value
        except Exception as e:
            logger.exception(f"Failed to transition assessment {assessment_id} to run_review: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to transition assessment status."
            )

    async def transition_to_final_approval(
        self, assessment_id: int, user_id: str
    ) -> str:
        """
        All reviewers approved → pending final approval from approvers for final_approval stage.
        Creates new approver rows with stage='final_approval' and action='pending' for existing approvers.
        """
        try:
            # Get baseline approvers from creation_approval stage only.
            approvers_result = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENT_APPROVERS,
                [None, assessment_id, None, ApproverStage.CREATION_APPROVAL.value, None, True, 1, 1000],
            )
            approver_emails = sorted(
                {
                    str(row.get("email")).strip().lower()
                    for row in (approvers_result[0] if approvers_result and approvers_result[0] else [])
                    if row.get("email")
                }
            )
            
            # Create new approver rows for final_approval stage with action=pending
            if approver_emails:
                await self.assign_approvers_for_stage(
                    assessment_id=assessment_id,
                    stage=ApproverStage.FINAL_APPROVAL,
                    approver_emails=approver_emails,
                    user_id=user_id,
                )
            
            # Transition assessment status
            await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ASSESSMENT,
                [
                    assessment_id, None, None,
                    AssessmentStatus.PENDING_FINAL_APPROVAL.value,
                    None, None, user_id
                ]
            )
            return AssessmentStatus.PENDING_FINAL_APPROVAL.value
        except Exception as e:
            logger.exception(f"Failed to transition assessment {assessment_id} to final_approval: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to transition assessment status."
            )

    async def transition_to_completed(
        self, assessment_id: int, user_id: str
    ) -> str:
        """All approvers approved → assessment completed."""
        try:
            await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ASSESSMENT,
                [
                    assessment_id, None, None,
                    AssessmentStatus.COMPLETED.value,
                    None, None, user_id
                ]
            )
            return AssessmentStatus.COMPLETED.value
        except Exception as e:
            logger.exception(f"Failed to transition assessment {assessment_id} to completed: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to transition assessment status."
            )

    async def transition_to_rejected(
        self, assessment_id: int, rejection_type: str, user_id: str
    ) -> str:
        """
        Transition to rejection state.
        rejection_type: 'creation_review_rejected', 'run_review_rejected', 'final_rejected'
        """
        try:
            await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ASSESSMENT,
                [
                    assessment_id, None, None,
                    rejection_type,
                    None, None, user_id
                ]
            )
            return rejection_type
        except Exception as e:
            logger.exception(f"Failed to transition assessment {assessment_id} to {rejection_type}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to transition assessment status."
            )

    # ========== Actioner Management ==========

    async def _deactivate_assessment_actioners(
        self,
        assessment_id: int,
        user_id: str
    ) -> None:
        """
        Deactivate all existing actioners for an assessment.
        When a new assignment is made, old actioners are marked as inactive.
        Uses the new reviewer/approver/control_owner tables.
        """
        try:
            # Deactivate all reviewers for this assessment
            await self.db_service.call_procedure(
                StoredProcedures.DEACTIVATE_ASSESSMENT_REVIEWERS,
                [assessment_id, user_id]
            )
            
            # Deactivate all approvers for this assessment
            await self.db_service.call_procedure(
                StoredProcedures.DEACTIVATE_ASSESSMENT_APPROVERS,
                [assessment_id, user_id]
            )
            
            # Deactivate all control owners for this assessment
            await self.db_service.call_procedure(
                StoredProcedures.DEACTIVATE_ASSESSMENT_CONTROL_OWNERS,
                [assessment_id, user_id]
            )
        except Exception as e:
            logger.exception(f"Error deactivating actioners for assessment {assessment_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to deactivate existing actioners."
            )

    async def assign_actioners_to_assessment(
        self,
        assessment_id: int,
        control_owner_email: str,
        reviewer_emails: List[str],
        approver_emails: List[str],
        user_id: str
    ) -> Dict[str, Any]:
        """
        Assign control owner, reviewers, and approvers to an assessment.
        If actioners already exist, they are deactivated and new ones are created.
        Uses the new three-table design with stage-aware tracking.
        
        - Reviewers are assigned to 'creation_review' stage with action='pending'
        - Approvers are assigned to 'creation_approval' stage with action='pending'
        - Control owner is assigned (no stage)
        
        Returns dict with control_owner, reviewers, approvers.
        """
        try:
            # 0. Deactivate existing actioners for this assessment
            await self._deactivate_assessment_actioners(assessment_id, user_id)
            
            results = {
                "control_owner": None,
                "reviewers": [],
                "approvers": []
            }

            # 1. Assign control owner (no stage)
            co_result = await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ASSESSMENT_CONTROL_OWNER,
                [
                    None,                      # p_id (NULL = INSERT)
                    assessment_id,             # p_assessment_id
                    control_owner_email,       # p_email
                    True,                      # p_is_active
                    user_id                    # p_user_id
                ]
            )
            if not co_result or not co_result[0] or 'id' not in co_result[0][0]:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to assign control owner."
                )
            co_id = self._extract_upsert_id(co_result)
            if co_id is None:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to resolve inserted control owner id.",
                )
            results["control_owner"] = await self._fetch_control_owner_by_id(co_id)

            # 2. Assign reviewers to creation_review stage with action=pending
            for reviewer_email in reviewer_emails:
                rev_result = await self.db_service.call_procedure(
                    StoredProcedures.UPSERT_ASSESSMENT_REVIEWER,
                    [
                        None,                               # p_id (NULL = INSERT)
                        assessment_id,                      # p_assessment_id
                        reviewer_email,                     # p_email
                        ReviewerStage.CREATION_REVIEW.value,# p_stage
                        ActionState.PENDING.value,          # p_action
                        None,                               # p_comments
                        True,                               # p_is_active
                        user_id                             # p_user_id
                    ]
                )
                if not rev_result or not rev_result[0] or 'id' not in rev_result[0][0]:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Failed to assign reviewer."
                    )
                rev_id = self._extract_upsert_id(rev_result)
                if rev_id is None:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Failed to resolve inserted reviewer id.",
                    )
                results["reviewers"].append(await self._fetch_reviewer_by_id(rev_id))

            # 3. Assign approvers to creation_approval stage with action=pending
            for approver_email in approver_emails:
                app_result = await self.db_service.call_procedure(
                    StoredProcedures.UPSERT_ASSESSMENT_APPROVER,
                    [
                        None,                                  # p_id (NULL = INSERT)
                        assessment_id,                         # p_assessment_id
                        approver_email,                        # p_email
                        ApproverStage.CREATION_APPROVAL.value, # p_stage
                        ActionState.PENDING.value,             # p_action
                        None,                                  # p_comments
                        True,                                  # p_is_active
                        user_id                                # p_user_id
                    ]
                )
                if not app_result or not app_result[0] or 'id' not in app_result[0][0]:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Failed to assign approver."
                    )
                app_id = self._extract_upsert_id(app_result)
                if app_id is None:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Failed to resolve inserted approver id.",
                    )
                results["approvers"].append(await self._fetch_approver_by_id(app_id))

            return results

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to assign actioners to assessment {assessment_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to assign actioners."
            )

    async def get_assessment_actioners(
        self, assessment_id: int
    ) -> Dict[str, List[Any]]:
        """
        Retrieve all actioners for an assessment from new tables (reviewers, approvers, control owner).
        Returns dict with control_owner (single or list), reviewers, approvers.
        """
        try:
            result = {
                "control_owner": None,
                "reviewers": [],
                "approvers": []
            }
            
            # Get control owner(s)
            co_result = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENT_CONTROL_OWNERS,
                [None, assessment_id, None, True, 1, 1000]
            )
            if co_result and co_result[0]:
                result["control_owner"] = [
                    AssessmentControlOwnerInDB(**row) for row in co_result[0]
                ]
            
            # Get reviewers (all stages) — keep only the latest row per (email, stage)
            rev_result = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENT_REVIEWERS,
                [None, assessment_id, None, None, None, True, 1, 1000]
            )
            if rev_result and rev_result[0]:
                latest_rev_by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
                for row in rev_result[0]:
                    email = str(row.get("email") or "").strip().lower()
                    stage_val = row.get("stage")
                    stage_key = (
                        stage_val.value if hasattr(stage_val, "value") else str(stage_val or "")
                    ).strip().lower()
                    if not email or not stage_key:
                        continue
                    key = (email, stage_key)
                    current = latest_rev_by_key.get(key)
                    if current is None or int(row.get("id") or 0) > int(current.get("id") or 0):
                        latest_rev_by_key[key] = row
                result["reviewers"] = [
                    AssessmentReviewerInDB(**row) for row in latest_rev_by_key.values()
                ]

            # Get approvers (all stages) — keep only the latest row per (email, stage)
            app_result = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENT_APPROVERS,
                [None, assessment_id, None, None, None, True, 1, 1000]
            )
            if app_result and app_result[0]:
                latest_app_by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
                for row in app_result[0]:
                    email = str(row.get("email") or "").strip().lower()
                    stage_val = row.get("stage")
                    stage_key = (
                        stage_val.value if hasattr(stage_val, "value") else str(stage_val or "")
                    ).strip().lower()
                    if not email or not stage_key:
                        continue
                    key = (email, stage_key)
                    current = latest_app_by_key.get(key)
                    if current is None or int(row.get("id") or 0) > int(current.get("id") or 0):
                        latest_app_by_key[key] = row
                result["approvers"] = [
                    AssessmentApproverInDB(**row) for row in latest_app_by_key.values()
                ]
            
            return result

        except Exception as e:
            logger.exception(f"Failed to retrieve assessment actioners for {assessment_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve actioners."
            )

    async def record_reviewer_action(
        self,
        assessment_id: int,
        reviewer_email: str,
        stage: ReviewerStage,
        action: ActionState,
        comments: Optional[str],
        user_id: str,
    ) -> AssessmentReviewerInDB:
        """
        Record a reviewer action (approval, rejection) for a specific stage.
        Updates the existing active reviewer row for (assessment_id, email, stage)
        if one exists; otherwise inserts a new row. This guarantees a single
        active row per (email, stage) so listing endpoints don't show duplicates.
        """
        try:
            normalized_email = str(reviewer_email or "").strip().lower()
            existing_id = await self._find_existing_reviewer_row_id(
                assessment_id=assessment_id,
                email=normalized_email,
                stage=stage.value,
            )

            result = await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ASSESSMENT_REVIEWER,
                [
                    existing_id,                   # p_id (NULL = INSERT, else UPDATE)
                    assessment_id,                 # p_assessment_id
                    normalized_email,              # p_email
                    stage.value,                   # p_stage
                    action.value,                  # p_action (pending|approved|rejected)
                    comments,                      # p_comments
                    True,                          # p_is_active
                    user_id                        # p_user_id (who performed the action)
                ]
            )

            if not result or not result[0] or 'id' not in result[0][0]:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to record reviewer action."
                )
            reviewer_id = self._extract_upsert_id(result)
            if reviewer_id is None:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to resolve reviewer action id.",
                )
            return await self._fetch_reviewer_by_id(reviewer_id)

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to record reviewer action for assessment {assessment_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to record reviewer action."
            )

    async def record_approver_action(
        self,
        assessment_id: int,
        approver_email: str,
        stage: ApproverStage,
        action: ActionState,
        comments: Optional[str],
        user_id: str,
    ) -> AssessmentApproverInDB:
        """
        Record an approver action (approval, rejection) for a specific stage.
        Updates the existing active approver row for (assessment_id, email, stage)
        if one exists; otherwise inserts a new row. This guarantees a single
        active row per (email, stage) so listing endpoints don't show duplicates.
        """
        try:
            normalized_email = str(approver_email or "").strip().lower()
            existing_id = await self._find_existing_approver_row_id(
                assessment_id=assessment_id,
                email=normalized_email,
                stage=stage.value,
            )

            result = await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ASSESSMENT_APPROVER,
                [
                    existing_id,                   # p_id (NULL = INSERT, else UPDATE)
                    assessment_id,                 # p_assessment_id
                    normalized_email,              # p_email
                    stage.value,                   # p_stage
                    action.value,                  # p_action (pending|approved|rejected)
                    comments,                      # p_comments
                    True,                          # p_is_active
                    user_id                        # p_user_id (who performed the action)
                ]
            )

            if not result or not result[0] or 'id' not in result[0][0]:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to record approver action."
                )
            approver_id = self._extract_upsert_id(result)
            if approver_id is None:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to resolve approver action id.",
                )
            return await self._fetch_approver_by_id(approver_id)

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to record approver action for assessment {assessment_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to record approver action."
            )

    async def assign_reviewers_for_stage(
        self,
        assessment_id: int,
        stage: ReviewerStage,
        reviewer_emails: List[str],
        user_id: str,
    ) -> List[AssessmentReviewerInDB]:
        """
        Assign reviewers to a specific stage (e.g., run_review).
        Creates new reviewer rows with action='pending' for the stage.
        """
        try:
            result = []
            for reviewer_email in reviewer_emails:
                reviewer = await self.record_reviewer_action(
                    assessment_id=assessment_id,
                    reviewer_email=reviewer_email,
                    stage=stage,
                    action=ActionState.PENDING,
                    comments=None,
                    user_id=user_id,
                )
                result.append(reviewer)
            return result

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to assign reviewers for stage {stage.value} to assessment {assessment_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to assign reviewers for {stage.value}."
            )

    async def assign_approvers_for_stage(
        self,
        assessment_id: int,
        stage: ApproverStage,
        approver_emails: List[str],
        user_id: str,
    ) -> List[AssessmentApproverInDB]:
        """
        Assign approvers to a specific stage (e.g., final_approval).
        Creates new approver rows with action='pending' for the stage.
        """
        try:
            result = []
            for approver_email in approver_emails:
                approver = await self.record_approver_action(
                    assessment_id=assessment_id,
                    approver_email=approver_email,
                    stage=stage,
                    action=ActionState.PENDING,
                    comments=None,
                    user_id=user_id,
                )
                result.append(approver)
            return result

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to assign approvers for stage {stage.value} to assessment {assessment_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to assign approvers for {stage.value}."
            )

    async def record_workflow_action(
        self,
        assessment_id: int,
        actioner_id: int,
        action: WorkflowActionType,
        comments: Optional[str],
        user_id: str,
        actioned_by: Optional[str] = None,
        reviewer_id: Optional[int] = None,
    ) -> AssessmentWorkflowInDB:
        """
        Record a workflow action (approval, rejection, etc.).
        Uses actual DB column names: review and actioned_by.
        actioner_id = assessment_actioner.id.
        reviewer_id is accepted for backward compatibility in caller signatures
        but is not passed to DB because the live stored procedure expects 8 args.
        """
        try:
            result = await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ASSESSMENT_WORKFLOW,
                [
                    None,          # p_id (INSERT)
                    assessment_id, # p_assessment_id
                    reviewer_id,   # p_reviewer_id (legacy FK to assessment_workflow_reviewers)
                    actioned_by or user_id,  # p_actioned_by
                    self._db_review_value(action),  # p_review ('approve'|'reject')
                    comments,      # p_comments
                    True,          # p_is_active
                    user_id        # p_user_id
                ]
            )

            if not result or not result[0] or 'id' not in result[0][0]:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to record workflow action."
                )

            row = result[0][0]
            if {'assessment_id', 'review', 'created_on', 'created_by'}.issubset(row.keys()):
                return AssessmentWorkflowInDB(**row)

            workflow_id = row['id']
            fetch_result = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENT_WORKFLOW,
                [workflow_id, None, None, None, None, True, 1, 1]
            )
            if not fetch_result or not fetch_result[0]:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Workflow action recorded but could not fetch saved record."
                )

            return AssessmentWorkflowInDB(**fetch_result[0][0])

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to record workflow action for assessment {assessment_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to record action."
            )

    async def get_workflow_history(
        self, assessment_id: int
    ) -> List[AssessmentWorkflowInDB]:
        """Retrieve workflow history for an assessment."""
        try:
            result = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENT_WORKFLOW,
                [None, assessment_id, None, None, None, True, 1, 1000]
            )
            
            if result and result[0]:
                normalized_rows: List[Dict[str, Any]] = []
                for row in result[0]:
                    normalized_row = dict(row)
                    normalized_row["review"] = self._normalize_review_from_db(row.get("review"))
                    normalized_rows.append(normalized_row)
                return [AssessmentWorkflowInDB(**row) for row in normalized_rows]
            return []

        except Exception as e:
            logger.exception(f"Failed to retrieve workflow history for assessment {assessment_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve workflow history."
            )

    # ========== Validation Helpers ==========

    async def validate_assessment_can_be_run(
        self, assessment_id: int
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if assessment can be run.
        Returns: (can_run, error_message)
        Constraints:
        - Max runs per assessment based on org settings.retry_count (fallback: 3)
        """
        try:
            # Get current assessment
            assessment_result = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENTS,
                [assessment_id, None, None, None, True, 1, 1]
            )
            if not assessment_result or not assessment_result[0]:
                return False, "Assessment not found."

            run_count = await self.get_assessment_run_count(assessment_id)
            allowed_attempts = await self.get_allowed_run_attempts(assessment_id)
            if run_count >= allowed_attempts:
                return False, f"Maximum run attempts ({allowed_attempts}) reached."

            return True, None

        except Exception as e:
            logger.exception(f"Error validating assessment {assessment_id}: {e}")
            return False, f"Validation error: {str(e)}"

    async def get_assessment_run_count(self, assessment_id: int) -> int:
        """Get total number of times assessment has been run."""
        try:
            versions_result = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSESSMENT_VERSIONS,
                [None, assessment_id, None, None, 1, 1000]
            )
            return len(versions_result[0]) if versions_result and versions_result[0] else 0
        except Exception as e:
            logger.exception(f"Error getting run count for assessment {assessment_id}: {e}")
            return 0

    async def get_assessment_rejection_count(self, assessment_id: int) -> int:
        """Get total number of rejections (uses 'review' column)."""
        try:
            workflow_history = await self.get_workflow_history(assessment_id)
            return sum(
                1
                for row in workflow_history
                if row.review == WorkflowActionType.REJECT
            )
        except Exception as e:
            logger.exception(f"Error getting rejection count for assessment {assessment_id}: {e}")
            return 0

    async def get_allowed_run_attempts(self, assessment_id: int) -> int:
        """
        Product rule: max total versions is always 3.
        """
        return 3

    async def get_final_approval_rejection_count(self, assessment_id: int) -> int:
        """Count reject actions performed by approvers."""
        try:
            workflow_history = await self.get_workflow_history(assessment_id)
            actioners_by_role = await self.get_assessment_actioners(assessment_id)
            approver_ids = {a.id for a in actioners_by_role.get("approvers", [])}
            approver_emails = {
                str(a.email).strip().lower()
                for a in actioners_by_role.get("approvers", [])
                if a.email
            }
            return sum(
                1
                for row in workflow_history
                if (
                    row.actioner_id in approver_ids
                    or str(row.actioned_by or "").strip().lower() in approver_emails
                )
                and row.review == WorkflowActionType.REJECT
            )
        except Exception as e:
            logger.exception("Error getting final approval rejection count for assessment %s: %s", assessment_id, e)
            return 0

    async def have_all_actioners_approved(
        self,
        assessment_id: int,
        role: AssessmentActionerRole,
        stage: Optional[str] = None,
    ) -> bool:
        """
        Return True only when every active actioner in a role has latest action=approved FOR A STAGE.
        
        For reviewers/approvers: stage is required (e.g., 'creation_review', 'creation_approval')
        For control owner: stage is ignored (no stages for control owner)
        """
        try:
            if role == AssessmentActionerRole.REVIEWER and stage:
                # Check if all reviewers in this stage have approved
                rev_result = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_ASSESSMENT_REVIEWERS,
                    [None, assessment_id, None, stage, None, True, 1, 1000]
                )
                if not rev_result or not rev_result[0]:
                    return False
                
                reviewers = [AssessmentReviewerInDB(**row) for row in rev_result[0]]
                if not reviewers:
                    return False
                
                # Get latest action per email (most recent row)
                latest_by_email: Dict[str, Tuple[int, ActionState]] = {}
                for reviewer in reviewers:
                    email = str(reviewer.email).strip().lower()
                    current = latest_by_email.get(email)
                    if current is None or reviewer.id > current[0]:
                        latest_by_email[email] = (reviewer.id, reviewer.action)
                
                # Check if all have approved
                return all(state[1] == ActionState.APPROVED for state in latest_by_email.values())

            elif role == AssessmentActionerRole.APPROVER and stage:
                # Check if all approvers in this stage have approved
                app_result = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_ASSESSMENT_APPROVERS,
                    [None, assessment_id, None, stage, None, True, 1, 1000]
                )
                if not app_result or not app_result[0]:
                    return False
                
                approvers = [AssessmentApproverInDB(**row) for row in app_result[0]]
                if not approvers:
                    return False
                
                # Get latest action per email
                latest_by_email: Dict[str, Tuple[int, ActionState]] = {}
                for approver in approvers:
                    email = str(approver.email).strip().lower()
                    current = latest_by_email.get(email)
                    if current is None or approver.id > current[0]:
                        latest_by_email[email] = (approver.id, approver.action)
                
                # Check if all have approved
                return all(state[1] == ActionState.APPROVED for state in latest_by_email.values())

            else:
                # Unsupported role or missing stage
                return False

        except Exception as e:
            logger.exception(
                f"Error checking if all {role.value if hasattr(role, 'value') else role} approved for assessment {assessment_id}, stage {stage}: {e}"
            )
            return False

