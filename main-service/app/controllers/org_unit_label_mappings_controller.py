import logging
from typing import List
from fastapi import HTTPException, status
from app.services.database_service import DatabaseService
from app.core.stored_procedures import StoredProcedures

from app.models.org import OrgUnitLabelMappingCreate, OrgUnitLabelMappingResponse

logger = logging.getLogger(__name__)

# [DEPRECATED] Tags org units at the CONTROL level (assessment_label_mapping_id).
# Superseded by EvidenceScopeController (assessment-level evidence scope).
# Retained for backward compatibility with the legacy tagging endpoints.
class OrgUnitLabelMappingsController:
    def __init__(self, db_service: DatabaseService):
        self.db_service = db_service

    async def add_org_unit_label_mapping(
        self,
        control_mapping_id: int,
        data: OrgUnitLabelMappingCreate,
        user_id: str
    ) -> List[OrgUnitLabelMappingResponse]:
        created_tags = []
        try:
            for org_unit_id in data.org_unit_ids:
                params = [None, org_unit_id, control_mapping_id, True, user_id]
                results = await self.db_service.call_procedure(StoredProcedures.UPSERT_ORG_UNIT_ASSESSMENT_LABEL_MAPPING, params)
                new_id = results[0][0]['id']

                filter_results = await self.db_service.call_procedure(StoredProcedures.FILTER_ORG_UNIT_ASSESSMENT_LABEL_MAPPINGS, [new_id, None, None, True, 1, 1])
                created_tags.append(OrgUnitLabelMappingResponse(**filter_results[0][0]))

            return created_tags
        except Exception as e:
            logger.exception(f"Error during org unit tag creation for control {control_mapping_id}: {e}")
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Error creating one or more org unit tags.")


    async def delete_org_unit_label_mapping(
        self,
        assessment_label_mapping_id: int,
        data: OrgUnitLabelMappingCreate,
        user_id: str
    ):
        try:
            for org_unit_id in data.org_unit_ids:
                filter_results = await self.db_service.call_procedure(StoredProcedures.FILTER_ORG_UNIT_ASSESSMENT_LABEL_MAPPINGS, [None, org_unit_id, assessment_label_mapping_id, True, 1, 1])
                if filter_results and filter_results[0]:
                    tag_id_to_delete = filter_results[0][0]['id']
                    await self.db_service.call_procedure(StoredProcedures.DELETE_ORG_UNIT_ASSESSMENT_LABEL_MAPPING, [tag_id_to_delete, user_id])
        except Exception as e:
            logger.exception(f"Error during org unit tag deletion for control {assessment_label_mapping_id}: {e}")
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to delete one or more org unit tags.")


    async def update_org_unit_label_mapping(
        self,
        assessment_label_mapping_id: int,
        data: OrgUnitLabelMappingCreate,
        user_id: str
    ) -> List[OrgUnitLabelMappingResponse]:
        """
        Replaces the entire set of org unit tags for a given control.
        """
        try:
            # Step 1: Get the current set of active tags
            current_tags_results = await self.db_service.call_procedure(StoredProcedures.FILTER_ORG_UNIT_ASSESSMENT_LABEL_MAPPINGS, [None, None, assessment_label_mapping_id, True, 1, 1000])
            current_tags_map = {tag['org_unit_id']: tag['id'] for tag in (current_tags_results[0] if current_tags_results and current_tags_results[0] else [])}
            current_org_unit_ids = set(current_tags_map.keys())

            # Step 2: Get the desired new set of tags
            new_org_unit_ids = set(data.org_unit_ids)

            # Step 3: Calculate differences
            ids_to_add = new_org_unit_ids - current_org_unit_ids
            ids_to_delete = current_org_unit_ids - new_org_unit_ids

            # Step 4: Perform deletions
            for org_unit_id in ids_to_delete:
                tag_id_to_delete = current_tags_map[org_unit_id]
                await self.db_service.call_procedure(StoredProcedures.DELETE_ORG_UNIT_ASSESSMENT_LABEL_MAPPING, [tag_id_to_delete, user_id])

            # Step 5: Perform additions
            for org_unit_id in ids_to_add:
                await self.db_service.call_procedure(StoredProcedures.UPSERT_ORG_UNIT_ASSESSMENT_LABEL_MAPPING, [None, org_unit_id, assessment_label_mapping_id, True, user_id])

            # Step 6: Fetch and return the final state
            final_tags_results = await self.db_service.call_procedure(StoredProcedures.FILTER_ORG_UNIT_ASSESSMENT_LABEL_MAPPINGS, [None, None, assessment_label_mapping_id, True, 1, 1000])
            if not final_tags_results or not final_tags_results[0]:
                return []
            return [OrgUnitLabelMappingResponse(**tag) for tag in final_tags_results[0]]
        except Exception as e:
            logger.exception(f"Error during org unit tag update for control {assessment_label_mapping_id}: {e}")
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to update org unit tags.")