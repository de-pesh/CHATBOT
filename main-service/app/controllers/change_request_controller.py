import logging
from typing import List, Optional

from fastapi import HTTPException, status

from app.core.stored_procedures import StoredProcedures
from app.models.change import ChangeInDB
from app.models.change import ChangeRequestInDB, ApprovalStatus
from app.services.database_service import DatabaseService

logger = logging.getLogger(__name__)

class ChangeRequestController:
    """
    Controller for managing change-related operations.

    This class encapsulates the business logic for creating, retrieving,
    updating, and deleting changes, interacting with the database
    service to perform these actions.
    """

    def __init__(self, db_service: DatabaseService):
        self.db_service = db_service


    async def get_change_requests_by_filters(self, change_request_id: int = None, version_id: int = None, approval_status: Optional[ApprovalStatus] = None, page_number: int = 1, page_size: int = 10) -> List[ChangeRequestInDB]:
        """
        Retrieves all change requests for a given version id or status or both.
        """
        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_CHANGE_REQUESTS,
                [change_request_id, version_id, approval_status, True, page_number, page_size]  # Assuming a large page size to get all changes
            )

            if not results or not results[0]:
                return []
            
            return [ChangeRequestInDB(**change) for change in results[0]]
        except Exception as e:
            logger.exception(f"Error retrieving changes for change requests: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")


    async def update_change_request(self, change_request_id: int, updated_by: str) -> ChangeRequestInDB:
        """
        Updates an existing change request.

        Args:
            change_request_id (int): The ID of the change request to update.

        Returns:
            ChangeRequestInDB: The updated change request's data.

        Raises:
            HTTPException: If the change request is not found or cannot be updated.
        """
        try:
            existing_change_request = await self.get_change_requests_by_filters(change_request_id=change_request_id)

            exisiting_changes_db = await self.db_service.call_procedure(
                StoredProcedures.FILTER_CHANGES,
                [None, existing_change_request[0].id, None, None, None, True, 1, 1000000]  # Assuming a large page size to get all changes
            )

            exisiting_changes=[]
            if exisiting_changes_db and exisiting_changes_db[0]:
                exisiting_changes = [ChangeInDB(**change) for change in exisiting_changes_db[0]]

            status = ApprovalStatus.REJECTED.value
            for change in exisiting_changes:
                if change.status == ApprovalStatus.APPROVED.value:
                    status = ApprovalStatus.APPROVED.value
                    break
                elif change.status == ApprovalStatus.OPEN.value:
                    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Approve/Reject all changes to proceed")
                
            if status == ApprovalStatus.REJECTED.value:
                version_id = existing_change_request[0].version_id
            else:
                old_new_mapping = {}
                old_version_id = existing_change_request[0].version_id
                version_results = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_VERSIONS,
                    [old_version_id, None, None, None, True, 1, 1]
                )
                new_version_result = await self.db_service.call_procedure(
                    StoredProcedures.UPSERT_VERSION,
                    [None, version_results[0][0].get("framework_id"), int(version_results[0][0].get("number"))+1, True, True, updated_by] # id, framework_id, number, is_active, user_id
                )
                version_id = new_version_result[0][0].get("id")
                in_use_versions = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_VERSIONS,
                    [None, version_results[0][0].get("framework_id"), None, True, True, 1, 1000000]
                )
                for in_use_version in in_use_versions[0]:
                    if in_use_version.get("id") != version_id:
                        new_version_result = await self.db_service.call_procedure(
                            StoredProcedures.UPSERT_VERSION,
                            [in_use_version.get("id"), None, None, False, True, updated_by] # id, framework_id, number, is_active, user_id
                        )
                version_files = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_VERSION_FILES,
                    [None, old_version_id, None, True, 1, 10000]
                )
                for file in version_files[0]:
                    version_file_id = await self.db_service.call_procedure(
                        StoredProcedures.UPSERT_VERSION_FILE,
                        [None, version_id, file.get("file_id"), True, updated_by]
                    )
                    old_new_mapping[file["id"]] = version_file_id[0][0]["id"]
                
                temp_nodes = []
                for key in old_new_mapping.keys():
                    temp_results = await self.db_service.call_procedure(
                        StoredProcedures.FILTER_TEMP_NODES,
                        [None, key, None, None, None, None, None, True, 1, 100000]
                    )
                    temp_nodes+=temp_results[0]
                
                for temp_node in temp_nodes:
                    await self.db_service.call_procedure(
                        StoredProcedures.UPSERT_NODE,
                        [None, old_new_mapping[temp_node["version_file_id"]], temp_node["node_id"], temp_node["label_id"], temp_node["name"], temp_node["description"], temp_node["guidelines"], temp_node["corresponding_question"], temp_node["label_type"], temp_node["parent_node_id"], True, updated_by]
                    )
                    await self.db_service.call_procedure(
                        StoredProcedures.UPSERT_TEMP_NODES,
                        [
                            temp_node["id"],
                            old_new_mapping[temp_node["version_file_id"]],
                            temp_node["node_id"],
                            temp_node["label_id"],
                            temp_node["name"],
                            temp_node["description"],
                            temp_node["guidelines"],
                            temp_node["corresponding_question"],
                            temp_node["label_type"],
                            temp_node["parent_node_id"],
                            True,
                            updated_by,
                        ]
                    )
    
            results = await self.db_service.call_procedure(
                StoredProcedures.UPSERT_CHANGE_REQUEST,
                [
                    change_request_id,
                    None,
                    status,
                    True,
                    updated_by
                ]
            )
            if not results or not results[0]:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update change request.")
            
            results = await self.db_service.call_procedure(
                StoredProcedures.UPSERT_CHANGE_REQUEST,
                [
                    None,
                    version_id,
                    ApprovalStatus.OPEN.value,
                    True,
                    updated_by
                ]
            )
            if not results or not results[0]:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create change request.")
            
            response = await self.get_change_requests_by_filters(change_request_id=change_request_id)
            
            return response[0]
        
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error updating change request {change_request_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")
