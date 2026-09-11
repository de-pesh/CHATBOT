import logging
from typing import List
from fastapi import HTTPException, status
from app.services.database_service import DatabaseService
from app.core.stored_procedures import StoredProcedures

from app.models.files import AssetLabelMappingCreate, AssetLabelMappingResponse

logger = logging.getLogger(__name__)

# [DEPRECATED] Tags assets at the CONTROL level (assessment_label_mapping_id).
# Superseded by EvidenceScopeController (assessment-level evidence scope).
# Retained for backward compatibility with the legacy tagging endpoints.
class AssetLabelMappingsController:
    def __init__(self, db_service: DatabaseService):
        self.db_service = db_service


    async def add_asset_label_mapping(
            self, 
            assessment_label_mapping_id: int, 
            data: AssetLabelMappingCreate, 
            user_id: str
        ) -> List[AssetLabelMappingResponse]:
        created_tags = []
        try:
            for asset_id in data.asset_ids:
                # Step 1: Create the new record
                params = [None, asset_id, assessment_label_mapping_id, True, user_id]
                results = await self.db_service.call_procedure(StoredProcedures.UPSERT_ASSET_ASSESSMENT_LABEL_MAPPING, params)
                new_id = results[0][0]['id']

                # Step 2: Fetch the complete record using the new ID
                filter_results = await self.db_service.call_procedure(StoredProcedures.FILTER_ASSET_ASSESSMENT_LABEL_MAPPINGS, [new_id, None, None, True, 1, 1])
                
                # Step 3: Serialize the full record into the rich response model
                created_tags.append(AssetLabelMappingResponse(**filter_results[0][0]))

            return created_tags
        except Exception as e:
            logger.exception(f"Error during asset tag creation for control {assessment_label_mapping_id}: {e}")
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to create one or more asset tags.")

    async def delete_asset_label_mapping(
            self,
            assessment_label_mapping_id: int,
            data: AssetLabelMappingCreate,
            user_id: str
        ):
        try:
            for asset_id in data.asset_ids:
                filter_results = await self.db_service.call_procedure(StoredProcedures.FILTER_ASSET_ASSESSMENT_LABEL_MAPPINGS, [None, asset_id, assessment_label_mapping_id, True, 1, 1])
                if filter_results and filter_results[0]:
                    tag_id_to_delete = filter_results[0][0]['id']
                    await self.db_service.call_procedure(StoredProcedures.DELETE_ASSET_ASSESSMENT_LABEL_MAPPING, [tag_id_to_delete, user_id])
        except Exception as e:
            logger.exception(f"Error during asset tag deletion for control {assessment_label_mapping_id}: {e}")
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to delete one or more asset tags.")


    async def update_asset_label_mapping(
        self,
        assessment_label_mapping_id: int,
        data: AssetLabelMappingCreate,
        user_id: str
    ) -> List[AssetLabelMappingResponse]:
        """
        Replaces the entire set of asset tags for a given control.
        """
        try:
            # Step 1: Get the current set of active tags for this control
            current_tags_results = await self.db_service.call_procedure(StoredProcedures.FILTER_ASSET_ASSESSMENT_LABEL_MAPPINGS, [None, None, assessment_label_mapping_id, True, 1, 1000])
            current_tags_map = {tag['asset_id']: tag['id'] for tag in (current_tags_results[0] if current_tags_results and current_tags_results[0] else [])}
            current_asset_ids = set(current_tags_map.keys())

            # Step 2: Get the desired new set of tags from the payload
            new_asset_ids = set(data.asset_ids)

            # Step 3: Calculate what to add and what to delete
            ids_to_add = new_asset_ids - current_asset_ids
            ids_to_delete = current_asset_ids - new_asset_ids

            # Step 4: Perform deletions (soft-delete)
            for asset_id in ids_to_delete:
                tag_id_to_delete = current_tags_map[asset_id]
                await self.db_service.call_procedure(StoredProcedures.DELETE_ASSET_ASSESSMENT_LABEL_MAPPING, [tag_id_to_delete, user_id])

            # Step 5: Perform additions
            for asset_id in ids_to_add:
                await self.db_service.call_procedure(StoredProcedures.UPSERT_ASSET_ASSESSMENT_LABEL_MAPPING, [None, asset_id, assessment_label_mapping_id, True, user_id])

            # Step 6: Fetch and return the final, complete list of active tags
            final_tags_results = await self.db_service.call_procedure(StoredProcedures.FILTER_ASSET_ASSESSMENT_LABEL_MAPPINGS, [None, None, assessment_label_mapping_id, True, 1, 1000])
            if not final_tags_results or not final_tags_results[0]:
                return []
            return [AssetLabelMappingResponse(**tag) for tag in final_tags_results[0]]

        except Exception as e:
            logger.exception(f"Error during asset tag update for control {assessment_label_mapping_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update asset tags.")