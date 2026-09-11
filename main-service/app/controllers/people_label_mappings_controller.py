import logging
from typing import List
from fastapi import HTTPException, status
from app.services.database_service import DatabaseService
from app.core.stored_procedures import StoredProcedures

from app.models.files import PeopleLabelMappingCreate, PeopleLabelMappingResponse

logger = logging.getLogger(__name__)

# [DEPRECATED] Tags people at the CONTROL level (assessment_label_mapping_id).
# Superseded by EvidenceScopeController (assessment-level evidence scope).
# Retained for backward compatibility with the legacy tagging endpoints.
class PeopleLabelMappingsController:
    def __init__(self, db_service: DatabaseService):
        self.db_service = db_service


    async def create_people_label_mapping(
            self,
            control_mapping_id: int,
            data: PeopleLabelMappingCreate,
            user_id: str
        ) -> List[PeopleLabelMappingResponse]:
        created_tags = []
        try:
            for email in data.emails:
                params = [None, email, control_mapping_id, True, user_id]
                results = await self.db_service.call_procedure(StoredProcedures.UPSERT_PEOPLE_ASSESSMENT_LABEL_MAPPING, params)
                new_id = results[0][0]['id']

                filter_results = await self.db_service.call_procedure(StoredProcedures.FILTER_PEOPLE_ASSESSMENT_LABEL_MAPPINGS, [new_id, None, None, True, 1, 1])
                created_tags.append(PeopleLabelMappingResponse(**filter_results[0][0]))
                
            return created_tags
        except Exception as e:
            logger.exception(f"Error during people tag creation for control {control_mapping_id}: {e}")
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to create one or more people tags.")


    async def delete_people_label_mapping(
        self,
        assessment_label_mapping_id: int,
        data: PeopleLabelMappingCreate,
        user_id: str
    ):
        try:
            for email in data.emails:
                filter_results = await self.db_service.call_procedure(StoredProcedures.FILTER_PEOPLE_ASSESSMENT_LABEL_MAPPINGS, [None, email, assessment_label_mapping_id, True, 1, 1])
                if filter_results and filter_results[0]:
                    tag_id_to_delete = filter_results[0][0]['id']
                    await self.db_service.call_procedure(StoredProcedures.DELETE_PEOPLE_ASSESSMENT_LABEL_MAPPING, [tag_id_to_delete, user_id])
        except Exception as e:
            logger.exception(f"Error during people tag deletion for control {assessment_label_mapping_id}: {e}")
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to delete one or more people tags.")


    async def update_people_label_mapping(
        self,
        assessment_label_mapping_id: int,
        data: PeopleLabelMappingCreate,
        user_id: str
    ) -> List[PeopleLabelMappingResponse]:
        """
        Replaces the entire set of people tags for a given control.
        """
        try:
            # Step 1: Get the current set of active tags
            current_tags_results = await self.db_service.call_procedure(StoredProcedures.FILTER_PEOPLE_ASSESSMENT_LABEL_MAPPINGS, [None, None, assessment_label_mapping_id, True, 1, 1000])
            current_tags_map = {tag['email']: tag['id'] for tag in (current_tags_results[0] if current_tags_results and current_tags_results[0] else [])}
            current_emails = set(current_tags_map.keys())
            
            # Step 2: Get the desired new set of tags
            new_emails = set(data.emails)

            # Step 3: Calculate differences
            emails_to_add = new_emails - current_emails
            emails_to_delete = current_emails - new_emails

            # Step 4: Perform deletions
            for email in emails_to_delete:
                tag_id_to_delete = current_tags_map[email]
                await self.db_service.call_procedure(StoredProcedures.DELETE_PEOPLE_ASSESSMENT_LABEL_MAPPING, [tag_id_to_delete, user_id])

            # Step 5: Perform additions
            for email in emails_to_add:
                await self.db_service.call_procedure(StoredProcedures.UPSERT_PEOPLE_ASSESSMENT_LABEL_MAPPING, [None, email, assessment_label_mapping_id, True, user_id])
            
            # Step 6: Fetch and return the final state
            final_tags_results = await self.db_service.call_procedure(StoredProcedures.FILTER_PEOPLE_ASSESSMENT_LABEL_MAPPINGS, [None, None, assessment_label_mapping_id, True, 1, 1000])
            if not final_tags_results or not final_tags_results[0]:
                return []
            return [PeopleLabelMappingResponse(**tag) for tag in final_tags_results[0]]
        except Exception as e:
            logger.exception(f"Error during people tag update for control {assessment_label_mapping_id}: {e}")
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to update people tags.")