# app/controllers/evidence_mapping_controller.py

import logging
from typing import List
from fastapi import HTTPException, status

from app.core.stored_procedures import StoredProcedures
from app.models.assessment import AssessmentStatus
from app.models.files import (
    EvidenceMappingsCreatePayload,
    EvidenceMappingsDeletePayload,
    EvidenceMappingResponse
)
from app.services.database_service import DatabaseService

logger = logging.getLogger(__name__)

class EvidenceMappingController:
    def __init__(self, db_service: DatabaseService):
        self.db_service = db_service

    async def _validate_control_owner_can_map(self, assessment_id: int, user_id: str) -> None:
        assessment_result = await self.db_service.call_procedure(
            StoredProcedures.FILTER_ASSESSMENTS,
            [assessment_id, None, None, None, True, 1, 1],
        )
        if not assessment_result or not assessment_result[0]:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Assessment not found.")

        assessment_status = assessment_result[0][0].get("status")
        allowed_statuses = {
            AssessmentStatus.PENDING_EVIDENCE_MAPPING.value,
            AssessmentStatus.ASSESSMENT_IN_PROGRESS.value,
            AssessmentStatus.RUN_REVIEW_REJECTED.value,
            AssessmentStatus.FINAL_REJECTED.value,
        }
        if assessment_status not in allowed_statuses:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                (
                    f"Evidence mapping is not allowed in status '{assessment_status}'. "
                    "Allowed statuses: pending_evidence_mapping, assessment_in_progress, run_review_rejected, final_rejected."
                ),
            )

        control_owner_result = await self.db_service.call_procedure(
            StoredProcedures.FILTER_ASSESSMENT_CONTROL_OWNERS,
            [None, assessment_id, None, True, 1, 1],
        )
        if not control_owner_result or not control_owner_result[0]:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "No active control owner is assigned to this assessment.",
            )

        control_owner_email = str(control_owner_result[0][0].get("email") or "").strip().lower()
        if str(user_id).strip().lower() != control_owner_email:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Only the assigned control owner can map or delete evidence for this assessment.",
            )

    async def create_evidence_mappings(self, data: EvidenceMappingsCreatePayload, user_id: str) -> List[EvidenceMappingResponse]:
        """Creates multiple evidence-to-control mappings in a single transaction."""
        created_mappings = []
        # This entire loop should ideally be wrapped in a database transaction
        # to ensure all mappings are created or none are.
        try:
            await self._validate_control_owner_can_map(data.assessment_id, user_id)

            for file_id in data.file_ids:
                # Step 1: Create the new mapping record
                params = [
                    None,
                    data.assessment_id,
                    data.assessment_label_mapping_id,
                    file_id,
                    True,
                    user_id
                ]
                results = await self.db_service.call_procedure(StoredProcedures.UPSERT_TEMP_LABEL_MAPPING_WISE_FILE, params)
                new_id = results[0][0]['id']

                # Step 2: Fetch the complete record to return a rich response
                filter_results = await self.db_service.call_procedure(StoredProcedures.FILTER_TEMP_LABEL_MAPPING_WISE_FILES, [new_id, None, None, None, True, 1, 1])
                
                # Step 3: Serialize and append to the response list
                created_mappings.append(EvidenceMappingResponse(**filter_results[0][0]))

            return created_mappings
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error during bulk evidence mapping creation: {e}")
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to create one or more evidence mappings.")

    async def delete_evidence_mappings(self, data: EvidenceMappingsDeletePayload, user_id: str):
        """Deletes multiple evidence-to-control mappings by their unique IDs."""
        try:
            for mapping_id in data.mapping_ids:
                # Optional: Check if the mapping exists before attempting to delete
                check_results = await self.db_service.call_procedure(StoredProcedures.FILTER_TEMP_LABEL_MAPPING_WISE_FILES, [mapping_id, None, None, None, True, 1, 1])
                if not check_results or not check_results[0]:
                    logger.warning(f"Attempted to delete non-existent evidence mapping with ID {mapping_id}. Skipping.")
                    continue

                assessment_id = check_results[0][0].get("assessment_id")
                if assessment_id is not None:
                    await self._validate_control_owner_can_map(int(assessment_id), user_id)

                # Perform the soft-delete
                await self.db_service.call_procedure(StoredProcedures.DELETE_TEMP_LABEL_MAPPING_WISE_FILE, [mapping_id, user_id])
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error during bulk evidence mapping deletion: {e}")
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to delete one or more evidence mappings.")