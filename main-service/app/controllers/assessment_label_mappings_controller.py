import logging
from typing import List
from fastapi import HTTPException, status
from app.services.database_service import DatabaseService
from app.core.stored_procedures import StoredProcedures
from app.controllers.assessment_label_controller import AssessmentLabelController
from app.models.assessment import (
    LabelMappingInDB,
    LabelMappingReplaceRequest,
    BulkLabelMappingCreatePayload,
    BulkLabelMappingReplacePayload,
    ControlTaggingResponse,
)
from app.models.files import AssetLabelMappingResponse
from app.models.org import OrgUnitLabelMappingResponse
from app.models.files import PeopleLabelMappingResponse

logger = logging.getLogger(__name__)

class LabelMappingsController:
    def __init__(self, db_service: DatabaseService):
        self.db_service = db_service
    
    async def _get_label_mapping_by_id(self, label_mapping_id) -> LabelMappingInDB:
        result = await self.db_service.call_procedure(
            StoredProcedures.FILTER_ASSESSMENT_LABEL_MAPPINGS,
            [
                label_mapping_id,
                None,
                None,
                True,
                1,
                1
            ]
        )
        if not result or not result[0]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No label mapping found")
        return LabelMappingInDB(**result[0][0])

    async def _get_label_mapping_by_label_and_node(self, assessment_label_id: int, node_id: str) -> LabelMappingInDB | None:
        result = await self.db_service.call_procedure(
            StoredProcedures.FILTER_ASSESSMENT_LABEL_MAPPINGS,
            [
                None,
                assessment_label_id,
                node_id,
                True,
                1,
                1,
            ]
        )
        if not result or not result[0]:
            return None
        return LabelMappingInDB(**result[0][0])
        

    
    async def add_assessment_label_mapping(self, assessment_label_id: int, node_id: str, user_id: str) -> LabelMappingInDB:
        # check if assessment label is valid
        controller = AssessmentLabelController(self.db_service)
        await controller._get_assessment_label_by_id(assessment_label_id)
        # initiate assessment label mapping
        result = await self.db_service.call_procedure(
            StoredProcedures.UPSERT_ASSESSMENT_LABEL_MAPPING,
            [
                None,  # id
                assessment_label_id,  # assessment_label_id
                node_id,  # node_id
                True,  # is_active
                user_id  # user_id
            ]
        )
        if not result or not result[0]:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to create assessment label mapping.")
        
        row = result[0][0]
        mapping_id = row['id']

        full = await self._get_label_mapping_by_id(mapping_id)
        return LabelMappingInDB.model_validate(full)

    async def replace_assessment_label_mapping(
        self,
        label_mapping_id: int,
        mapping_data: LabelMappingReplaceRequest,
        user_id: str,
    ) -> LabelMappingInDB:
        existing_mapping = await self._get_label_mapping_by_id(label_mapping_id)

        if mapping_data.node_id == existing_mapping.node_id:
            return existing_mapping

        duplicate_mapping = await self._get_label_mapping_by_label_and_node(
            existing_mapping.assessment_label_id,
            mapping_data.node_id,
        )
        if duplicate_mapping and duplicate_mapping.id != label_mapping_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The requested control is already mapped to this assessment label.",
            )

        try:
            result = await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ASSESSMENT_LABEL_MAPPING,
                [
                    label_mapping_id,
                    existing_mapping.assessment_label_id,
                    mapping_data.node_id,
                    existing_mapping.is_active,
                    user_id,
                ]
            )
            if not result or not result[0] or 'id' not in result[0][0]:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Unable to replace assessment label mapping.",
                )
            return await self._get_label_mapping_by_id(label_mapping_id)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error replacing assessment label mapping {label_mapping_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")

    async def bulk_add_assessment_label_mappings(
        self,
        payload: BulkLabelMappingCreatePayload,
        user_id: str,
    ) -> List[LabelMappingInDB]:
        """Creates multiple control mappings for a single assessment label."""
        controller = AssessmentLabelController(self.db_service)
        await controller._get_assessment_label_by_id(payload.assessment_label_id)

        created_mappings = []
        try:
            for node_id in payload.node_ids:
                result = await self.db_service.call_procedure(
                    StoredProcedures.UPSERT_ASSESSMENT_LABEL_MAPPING,
                    [
                        None,
                        payload.assessment_label_id,
                        node_id,
                        True,
                        user_id,
                    ]
                )
                if not result or not result[0]:
                    logger.warning(f"Failed to create mapping for node {node_id} under label {payload.assessment_label_id}")
                    continue

                mapping_id = result[0][0]['id']
                mapping = await self._get_label_mapping_by_id(mapping_id)
                created_mappings.append(mapping)

            return created_mappings
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error during bulk assessment label mapping creation: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create one or more control mappings.",
            )

    async def bulk_replace_assessment_label_mappings(
        self,
        payload: BulkLabelMappingReplacePayload,
        user_id: str,
    ) -> List[LabelMappingInDB]:
        """Updates multiple control mappings in bulk."""
        updated_mappings = []
        try:
            for replacement in payload.replacements:
                label_mapping_id = replacement.get('label_mapping_id')
                node_id = replacement.get('node_id')

                if not label_mapping_id or not node_id:
                    logger.warning(f"Skipping incomplete replacement record: {replacement}")
                    continue

                try:
                    mapping = await self.replace_assessment_label_mapping(
                        label_mapping_id,
                        LabelMappingReplaceRequest(node_id=node_id),
                        user_id,
                    )
                    updated_mappings.append(mapping)
                except HTTPException as he:
                    logger.warning(f"Failed to update mapping {label_mapping_id}: {he.detail}")
                    continue

            return updated_mappings
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error during bulk assessment label mapping update: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update one or more control mappings.",
            )


    async def fetch_control_and_tagging(self, label_mapping_id: int) -> ControlTaggingResponse:
        """Aggregate the control mapping with its org-unit, people and asset tags.

        Used to re-hydrate the Org Unit / People / Asset dropdowns when viewing
        a previously saved assessment control row.
        """
        label_mapping = await self._get_label_mapping_by_id(label_mapping_id)

        try:
            org_unit_results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ORG_UNIT_ASSESSMENT_LABEL_MAPPINGS,
                [None, None, label_mapping_id, True, 1, 1000],
            )
            people_results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_PEOPLE_ASSESSMENT_LABEL_MAPPINGS,
                [None, None, label_mapping_id, True, 1, 1000],
            )
            asset_results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ASSET_ASSESSMENT_LABEL_MAPPINGS,
                [None, None, label_mapping_id, True, 1, 1000],
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error fetching tagging for label mapping {label_mapping_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to fetch control tagging.",
            )

        org_unit_rows = org_unit_results[0] if org_unit_results and org_unit_results[0] else []
        people_rows = people_results[0] if people_results and people_results[0] else []
        asset_rows = asset_results[0] if asset_results and asset_results[0] else []

        return ControlTaggingResponse(
            label_mapping=label_mapping,
            org_units=[OrgUnitLabelMappingResponse(**row) for row in org_unit_rows],
            people=[PeopleLabelMappingResponse(**row) for row in people_rows],
            assets=[AssetLabelMappingResponse(**row) for row in asset_rows],
        )


    async def delete_assessment_label_mapping(self, label_mapping_id: int, user_id: str) -> None:
        # check if active label_mapping_id exists
        await self._get_label_mapping_by_id(label_mapping_id)
        # soft delete assessment label mapping for a specific id
        try:
            await self.db_service.call_procedure(
                StoredProcedures.DELETE_ASSESSMENT_LABEL_MAPPING,
                [
                    label_mapping_id,
                    user_id
                ]
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error deleting assessment label mapping {label_mapping_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")

