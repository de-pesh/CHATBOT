import logging
import uuid
from typing import List, Optional

from app.config import Settings
from app.models.change import(
    ApprovalStatus,
    ChangeActionTypeEnum,
    ChangeCreatePayload,
    ChangeDetailResponse,
    ChangeInDB,
    ChangeUpdate,
    NodeDetailsInDB
)
from app.services.redis_service import RedisQueueService
from fastapi import HTTPException, status

from app.core.stored_procedures import StoredProcedures
from app.models.change import ChangeRequestInDB
from app.services.database_service import DatabaseService

logger = logging.getLogger(__name__)

class ChangeController:
    """
    Controller for managing change-related operations.

    This class encapsulates the business logic for creating, retrieving,
    updating, and deleting changes, interacting with the database
    service to perform these actions.
    """

    def __init__(
            self,
            db_service: DatabaseService,
            redis_service: Optional[RedisQueueService] = None
    ):
        self.db_service = db_service
        self.redis_service = redis_service


    async def _check_leaf_node(
        self,
        version_file_id: str,
        node_id: str
    ) -> bool:
        """
        Checking if the node is a leaf node.

        Args:
            version_file_id (str): Version File ID from which the node belongs to.
            node_id (str): Node ID of the node to be checked.

        Raises:
            HTTPException: If the change can not be executed.
        """
        try:
            temp_child_node_details = await self.db_service.call_procedure(
                StoredProcedures.FILTER_TEMP_NODES,
                [None, version_file_id, None, None, None, None, node_id, True, 1, 1000000000]
            )

            if temp_child_node_details and temp_child_node_details[0]:
                return False

            base_child_node_details = await self.db_service.call_procedure(
                StoredProcedures.FILTER_NODES,
                [None, version_file_id, None, None, None, None, node_id, True, 1, 1000000000]
            )

            if base_child_node_details and base_child_node_details[0]:
                return False

            return True
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error checking node - {node_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")
        

    async def _remove_children(
        self,
        version_file_id: str,
        queue: list,
        user_id: str
    ) -> None:
        """
        Removing children nodes.

        Args:
            version_file_id (str): Version File ID from which the node belongs to.
            node_id (str): Node ID of the node.

        Raises:
            HTTPException: If the change can not be executed.
        """
        try:
            while(len(queue)>0):
                node = queue.pop()
                node_details = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_NODES,
                    [None, version_file_id, None, None, None, None, node.get("node_id"), True, 1, 1000]
                )

                if node_details and node_details[0]:
                    queue.extend(node_details[0])

                await self.db_service.call_procedure(
                    StoredProcedures.UPSERT_TEMP_NODES,
                    [node.get("id"), None, None, None, None, None, None, None, None, None, False, user_id]
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error deleting children: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")
        

    async def _create_criteria(
        self,
        node_details: dict,
        settings: Settings,
        parent_node_details: Optional[dict] = None
    ) -> None:
        """
        Createcriterias for node.

        Args:
            version_file_id (str): Version File ID from which the node belongs to.
            node_id (str): Node ID of the node.

        Raises:
            HTTPException: If the change can not be executed.
        """
        try:
            if self.redis_service:
                leaf_name = node_details.get("name") or ""
                leaf_description = node_details.get("description") or ""
                parent_name = parent_node_details.get("name") if parent_node_details else ""
                parent_description = parent_node_details.get("description") if parent_node_details else ""

                # Build prompt context from bottom-most node and one level above.
                name_parts = [part for part in [parent_name, leaf_name] if part]
                desc_parts = [part for part in [parent_description, leaf_description] if part]
                NAME = " > ".join(name_parts)
                DESCRIPTION = "\n".join(desc_parts)
                GUIDELINES = node_details.get("guidelines")
                criteria_prompt= settings.CRITERIA_GENERATION_PROMPT
                final_criteria_prompt = criteria_prompt.replace("{NAME}", NAME).replace("{DESCRIPTION}", DESCRIPTION)
                message = {
                    "action_type": 0,
                    "version_file_id": node_details.get("version_file_id"),
                    "node_id": node_details.get("node_id"),
                    "prompt": final_criteria_prompt
                }
                await self.redis_service.send_message(message)
            logger.info(f"Sent control info to Redis queue for node_id: {node_details.get("node_id")}")
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error deleting children: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")
        
    
    async def _check_and_apply_change(
            self,
            existing_change: ChangeInDB,
            existing_change_node_details: dict,
            user_id: str,
            settings: Settings
        ) -> None:
        """
        Checking the node and applying the changes.

        Args:
            existing_change (ChangeInDB): Change details in the DB.

        Raises:
            HTTPException: If the change can not be executed.
        """
        try:
            if existing_change.action_type == ChangeActionTypeEnum.ADD.value:
                if existing_change_node_details.get("parent_node_id"):
                    parent_node_details = await self.db_service.call_procedure(
                        StoredProcedures.FILTER_TEMP_NODES,
                        [None, existing_change_node_details.get("version_file_id"), existing_change_node_details.get("parent_node_id"), None, None, None, None, True, 1, 10]
                    )
                    if not parent_node_details or not parent_node_details[0]:
                        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parent node not found.")
                    
                    parent_label_type = parent_node_details[0][0]["label_type"]

                    framework_config = await self.db_service.call_procedure(
                        StoredProcedures.FILTER_FRAMEWORK_CONFIGURATIONS,
                        [None, None, None, parent_label_type, True, 1, 1]
                    )

                    expected_label_type = framework_config[0][0]["id"]

                    if expected_label_type != existing_change_node_details.get("label_type"):
                        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incorrect node label type.")
                    
                    is_parent_leaf = await self._check_leaf_node(existing_change_node_details.get("version_file_id"), existing_change_node_details.get("parent_node_id"))

                    if is_parent_leaf:
                        child_of_parent_node_details = await self.db_service.call_procedure(
                            StoredProcedures.FILTER_TEMP_NODES,
                            [None, existing_change_node_details.get("version_file_id"), None, None, None, None, existing_change_node_details.get("parent_node_id"), True, 1, 10]
                        )
                        if child_of_parent_node_details and child_of_parent_node_details[0]:
                            await self._remove_children(existing_change_node_details.get("version_file_id"), child_of_parent_node_details[0], user_id)


                existing_temp_node = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_TEMP_NODES,
                    [None, existing_change_node_details.get("version_file_id"), existing_change_node_details.get("node_id"), None, None, None, None, True, 1, 1]
                )
                existing_temp_node_id = existing_temp_node[0][0].get("id") if existing_temp_node and existing_temp_node[0] else None

                temp_node_details = await self.db_service.call_procedure(
                    StoredProcedures.UPSERT_TEMP_NODES,
                    [existing_temp_node_id, existing_change_node_details.get("version_file_id"), existing_change_node_details.get("node_id"), existing_change_node_details.get("label_id"), existing_change_node_details.get("name"), existing_change_node_details.get("description"), existing_change_node_details.get("guidelines"), existing_change_node_details.get("corresponding_question"), existing_change_node_details.get("label_type"), existing_change_node_details.get("parent_node_id"), True, user_id]
                )
                # Fetch fresh node record to get the actual version_file_id assigned by the DB,
                # which may differ from the stale value stored in node_changes.
                fresh_node_result = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_TEMP_NODES,
                    [None, None, existing_change_node_details.get("node_id"), None, None, None, None, True, 1, 1]
                )
                node_for_criteria = fresh_node_result[0][0] if fresh_node_result and fresh_node_result[0] else existing_change_node_details
                is_new_node_leaf = await self._check_leaf_node(
                    node_for_criteria.get("version_file_id"),
                    node_for_criteria.get("node_id")
                )
                if is_new_node_leaf:
                    # Generate criteria only for bottom-most node with one-level parent context.
                    if node_for_criteria.get("parent_node_id"):
                        parent_node_details = await self.db_service.call_procedure(
                            StoredProcedures.FILTER_TEMP_NODES,
                            [None, node_for_criteria.get("version_file_id"), node_for_criteria.get("parent_node_id"), None, None, None, None, True, 1, 1]
                        )
                        if parent_node_details and parent_node_details[0]:
                            await self._create_criteria(node_for_criteria, settings, parent_node_details[0][0])
                        else:
                            await self._create_criteria(node_for_criteria, settings)
                    else:
                        await self._create_criteria(node_for_criteria, settings)

            else:
                node_details = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_TEMP_NODES,
                    [None, existing_change_node_details.get("version_file_id"), existing_change_node_details.get("node_id"), None, None, None, None, True, 1, 10]
                )
                if not node_details or not node_details[0]:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")
                
                if existing_change.action_type == ChangeActionTypeEnum.DELETE.value:
                    queue = []
                    queue.append(node_details[0][0])
                    await self._remove_children(
                        existing_change_node_details.get("version_file_id"),
                        queue,
                        user_id
                    )
                    if existing_change_node_details.get("parent_node_id"):
                        is_parent_leaf = await self._check_leaf_node(existing_change_node_details.get("version_file_id"), existing_change_node_details.get("parent_node_id"))
                        if is_parent_leaf:
                            parent_node_details = await self.db_service.call_procedure(
                                StoredProcedures.FILTER_TEMP_NODES,
                                [None, existing_change_node_details.get("version_file_id"), existing_change_node_details.get("parent_node_id"), None, None, None, None, True, 1, 10]
                            )
                            parent_node_context = None
                            if parent_node_details and parent_node_details[0] and parent_node_details[0][0].get("parent_node_id"):
                                grand_parent_details = await self.db_service.call_procedure(
                                    StoredProcedures.FILTER_TEMP_NODES,
                                    [None, existing_change_node_details.get("version_file_id"), parent_node_details[0][0].get("parent_node_id"), None, None, None, None, True, 1, 1]
                                )
                                if grand_parent_details and grand_parent_details[0]:
                                    parent_node_context = grand_parent_details[0][0]
                            await self._create_criteria(parent_node_details[0][0], settings, parent_node_context)
                elif existing_change.action_type == ChangeActionTypeEnum.EDIT.value:
                    is_leaf = await self._check_leaf_node(existing_change_node_details.get("version_file_id"), existing_change_node_details.get("node_id"))
                    if is_leaf:
                        child_node_details = await self.db_service.call_procedure(
                            StoredProcedures.FILTER_TEMP_NODES,
                            [None, existing_change_node_details.get("version_file_id"), None, None, None, None, existing_change_node_details.get("node_id"), True, 1, 10]
                        )
                        if child_node_details and child_node_details[0]:
                            await self._remove_children(existing_change_node_details.get("version_file_id"), child_node_details[0], user_id)
                    temp_node_details = await self.db_service.call_procedure(
                        StoredProcedures.UPSERT_TEMP_NODES,
                        [node_details[0][0].get("id"), None, None, None, existing_change_node_details.get("name"), existing_change_node_details.get("description"), existing_change_node_details.get("guidelines"), existing_change_node_details.get("corresponding_question"), None, None, True, user_id]
                    )
                    if is_leaf:
                        parent_node_context = None
                        # Use node_details[0][0] here — it has the correct version_file_id from DB,
                        # whereas existing_change_node_details may hold a stale version_file_id.
                        fresh_edit_node = node_details[0][0]
                        if fresh_edit_node.get("parent_node_id"):
                            parent_node_details = await self.db_service.call_procedure(
                                StoredProcedures.FILTER_TEMP_NODES,
                                [None, fresh_edit_node.get("version_file_id"), fresh_edit_node.get("parent_node_id"), None, None, None, None, True, 1, 1]
                            )
                            if parent_node_details and parent_node_details[0]:
                                parent_node_context = parent_node_details[0][0]
                        await self._create_criteria(fresh_edit_node, settings, parent_node_context)
                elif existing_change.action_type == ChangeActionTypeEnum.MOVE.value:
                    existing_parent_node_details = await self.db_service.call_procedure(
                        StoredProcedures.FILTER_TEMP_NODES,
                        [None, existing_change_node_details.get("version_file_id"), node_details[0][0]["parent_node_id"], None, None, None, None, True, 1, 10]
                    )
                    if not node_details or not node_details[0]:
                        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Old parent node not found.")
                    
                    new_parent_node_details = await self.db_service.call_procedure(
                        StoredProcedures.FILTER_TEMP_NODES,
                        [None, existing_change_node_details.get("version_file_id"), existing_change_node_details.get("parent_node_id"), None, None, None, None, True, 1, 10]
                    )
                    if not node_details or not node_details[0]:
                        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="New parent node not found.")

                    if existing_parent_node_details[0][0]["label_type"] != new_parent_node_details[0][0]["label_type"]:
                        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node parent label type must be the same.")
                    
                    temp_node_details = await self.db_service.call_procedure(
                        StoredProcedures.UPSERT_TEMP_NODES,
                        [node_details[0][0].get("id"), None, None, None, None, None, None, None, None, existing_change_node_details.get("parent_node_id"), True, user_id]
                    )
                    exisiting_label = temp_node_details.get("label_id")
                    queue = []
                    queue.append(node_details[0][0])
                    while(len(queue)>0):
                        node = queue.pop()
                        node_details = await self.db_service.call_procedure(
                            StoredProcedures.FILTER_NODES,
                            [None, existing_change_node_details.get("version_file_id"), None, None, None, None, node.get("node_id"), True, 1, 1000]
                        )

                        if node_details and node_details[0]:
                            queue.extend(node_details[0])

                        await self.db_service.call_procedure(
                            StoredProcedures.UPSERT_TEMP_NODES,
                            [node.get("id"), None, None, node.get("label_id").replace(exisiting_label, existing_change_node_details.get("label_id")), None, None, None, None, None, None, None, user_id]
                        )
                else:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Valid operation not found.")
                
            if existing_change.action_type == ChangeActionTypeEnum.ADD.value or existing_change.action_type == ChangeActionTypeEnum.EDIT.value:
                if self.redis_service:
                    NAME = existing_change_node_details.get("name")
                    DESCRIPTION = existing_change_node_details.get("description")
                    GUIDELINES = existing_change_node_details.get("guidelines")
                    question_prompt= settings.CORRESPONDING_QUESTION_PROMPT
                    final_question_prompt = question_prompt.replace("{NAME}", NAME)
                    message = {
                        "action_type": 2,
                        "version_file_id": existing_change_node_details.get("version_file_id"),
                        "node_id": existing_change_node_details.get("node_id"),
                        "prompt": final_question_prompt
                    }
                    await self.redis_service.send_message(message)
                logger.info(f"Sent control info to Redis queue for node_id: {existing_change_node_details.get("node_id")}")

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error approving change - {existing_change.id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")

    async def add_change_to_change_request(self, change_data: ChangeCreatePayload, user_id: str) -> ChangeInDB:
        """
        Adds a new change to an existing change request.

        Args:
            change_request_id (int): The ID of the change request.
            change_data (NewChangeModel): The data for the new change.
            user_id (str): The ID of the user adding the change.

        Returns:
            ChangeInDB: The created change's data.

        Raises:
            HTTPException: If the change cannot be created.
        """
        try:
            versions = await self.db_service.call_procedure(
                StoredProcedures.FILTER_VERSIONS,
                [None, change_data.framework_id, None, None, True, 1, 1000000]
            )
            if not versions or not versions[0]:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="No versions exists.")
            
            version_id = versions[0][-1]["id"]
            
            version_file_ids = await self.db_service.call_procedure(
                StoredProcedures.FILTER_VERSION_FILES,
                [
                    None,
                    version_id,
                    change_data.file_id,
                    True,
                    1,
                    1
                ]
            )
            if not version_file_ids or not version_file_ids[0]:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="No version file mapping exists.")
            
            version_file_id = version_file_ids[0][0]["id"]
            
            # First, verify the change request exists
            change_request_db = await self.db_service.call_procedure(
                StoredProcedures.FILTER_CHANGE_REQUESTS,
                [None, version_id, ApprovalStatus.OPEN.value, True, 1, 10]  # Assuming a large page size to get all changes
            )

            change_request = []
            if change_request_db and change_request_db[0]:
                change_request = [ChangeRequestInDB(**change_request) for change_request in change_request_db[0]]

            action_type = change_data.action_type
            label_id = change_data.node_data.label_id
            name = change_data.node_data.name
            description = change_data.node_data.description

            if change_data.action_type == ChangeActionTypeEnum.ADD.value:
                node_id = str(uuid.uuid4())
            else:
                node_id = change_data.node_data.node_id

            results = await self.db_service.call_procedure(
                StoredProcedures.UPSERT_CHANGE,
                [
                    None,
                    change_request[0].id,
                    ApprovalStatus.OPEN.value,
                    label_id,
                    action_type,
                    change_data.remarks,
                    True,
                    user_id
                ]
            )
            if not results or not results[0] or 'id' not in results[0][0]:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create change.")
            
            new_change_id = results[0][0]['id']

            node_change_results = await self.db_service.call_procedure(
                StoredProcedures.UPSERT_NODE_CHANGE,
                [
                    None,
                    new_change_id,
                    version_file_id,
                    node_id,
                    action_type,
                    label_id,
                    name,
                    description,
                    None,
                    change_data.node_data.label_type,
                    change_data.node_data.parent_node_id,
                    True,
                    user_id
                ]
            )
            if not node_change_results or not node_change_results[0] or 'id' not in node_change_results[0][0]:
                # If the node_change fails, we should ideally roll back the change creation.
                # For now, we'll just raise an error.
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create node change record.")
            
            new_node_change_id = node_change_results[0][0]['id']

            return await self.get_change_by_id(new_change_id)

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error adding change to request to version ID - {change_data.version_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")


    async def get_change_by_id(self, change_id: int) -> ChangeInDB:
        """
        Retrieves a change by its ID.
        """
        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_CHANGES,
                [change_id, None, None, None, None, None, None, None]
            )
            if not results or not results[0]:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Change not found.")
            return ChangeInDB(**results[0][0])
        except Exception as e:
            logger.exception(f"Error retrieving change {change_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")
        

    async def get_change_details_by_id(self, change_id: int) -> ChangeDetailResponse:
        """
        Retrieves a change by its ID.
        """
        try:
            response = {
                "old": {},
                "new": {}
            }
            change = await self.get_change_by_id(change_id)

            if change.action_type != ChangeActionTypeEnum.DELETE.value:
                new_details = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_NODE_CHANGES,
                    [None, change_id, None, None, None, None, True, 1, 1]
                )
                if not new_details or not new_details[0]:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Change not found.")
                
                response["new"] = {
                    "node_id": new_details[0][0].get("node_id"),
                    "label_id": new_details[0][0].get("label_id"),
                    "name": new_details[0][0].get("name"),
                    "description": new_details[0][0].get("description"),
                    "label_type": new_details[0][0].get("label_type"),
                    "parent_node_id": new_details[0][0].get("parent_node_id")
                }

            old_details = await self.db_service.call_procedure(
                StoredProcedures.FILTER_NODES,
                [None, None, new_details[0][0].get("node_id"), None, None, None, None, True, 1, 10]
            )
            if not old_details:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")
            
            if old_details[0]:
                response["old"] = {
                    "node_id": old_details[0][-1].get("node_id"),
                    "label_id": old_details[0][-1].get("label_id"),
                    "name": old_details[0][-1].get("name"),
                    "description": old_details[0][-1].get("description"),
                    "label_type": old_details[0][-1].get("label_type"),
                    "parent_node_id": old_details[0][0].get("parent_node_id")
                }
            return response

        except Exception as e:
            logger.exception(f"Error retrieving change {change_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")


    async def get_changes_by_version_id(self, version_id: int) -> List[ChangeInDB]:
        """
        Retrieves all changes for a given change request ID.
        """
        try:
            change_request_db = await self.db_service.call_procedure(
                StoredProcedures.FILTER_CHANGE_REQUESTS,
                [None, version_id, ApprovalStatus.OPEN.value, True, 1, 10]  # Assuming a large page size to get all changes
            )
            if not change_request_db or not change_request_db[0]:
                return []

            change_request = [ChangeRequestInDB(**change_request) for change_request in change_request_db[0]]
            if not change_request:
                return []

            results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_CHANGES,
                [None, change_request[0].id, None, None, None, True, 1, 1000000]  # Assuming a large page size to get all changes
            )
            if not results or not results[0]:
                return []
            return [ChangeInDB(**change) for change in results[0]]
        except Exception as e:
            logger.exception(f"Error retrieving changes for version {version_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")


    async def update_change(self, change_id: int, change_data: ChangeUpdate, user_id: str, settings: Settings) -> ChangeInDB:
        """
        Updates an existing change.

        Args:
            change_id (int): The ID of the change to update.
            change_data (ChangeUpdate): The data to update the change with.
            user_id (str): The ID of the user performing the update.

        Returns:
            ChangeInDB: The updated change data.

        Raises:
            HTTPException: If the change is not found or fails to update.
        """
        try:
            existing_change = await self.get_change_by_id(change_id)
            # Apply change effects only on first transition to APPROVED.
            should_apply_change = (
                change_data.status == ApprovalStatus.APPROVED.value
                and existing_change.status != ApprovalStatus.APPROVED.value
            )

            existing_change_node_details = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_NODE_CHANGES,
                    [None, change_id, None, None, None, None, True, 1, 1]
                )
            if not existing_change_node_details or not existing_change_node_details[0]:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Changed node details not found.")

            if should_apply_change:
                await self._check_and_apply_change(existing_change, existing_change_node_details[0][0], user_id, settings)

            results = await self.db_service.call_procedure(
                StoredProcedures.UPSERT_CHANGE,
                [change_id, None, change_data.status, None, None, change_data.remarks, True, user_id]
            )

            if not results or not results[0] or 'id' not in results[0][0]:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update change.")

            return await self.get_change_by_id(change_id)

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error updating change {change_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")


    async def get_node_details_by_id(self, node_id: str) -> NodeDetailsInDB:
        """
        Retrieves details for a specific node id
        Args:
            node_id (str): The unique identifier of the node.
        Returns:
            FrameworkNode: The node's data.     
        Raises:
            HTTPException: If the node is not found.
        """
        try:
            node_details = await self.db_service.call_procedure(
                StoredProcedures.FILTER_TEMP_NODES,
                [None, None, node_id, None, None, None, None, True, 1, 1]
            )
            if not node_details or not node_details[0]:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to find node for the id: {node_id}.")
            return NodeDetailsInDB(**node_details[0][0])
        
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error retrieving tree for framework: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")


    # async def get_label_items_by_id(self, label_id: int): 

    async def get_parent_label_items_by_id(self, label_type_id: int) -> List[NodeDetailsInDB]:
        """
        Retrieves a list of items (nodes) associated with the PARENT of the provided label type.

        1. Looks up the framework_configuration for the given label_type_id.
        2. Extracts the parent_id.
        3. Retrieves nodes where label_type matches that parent_id.

        Args:
            label_type_id (int): The child label type ID.

        Returns:
            List[NodeDetailsInDB]: List of nodes belonging to the parent label type.
        """
        try:
            # Step 1: Find the parent_id from framework_configurations
            # mapping args: id, framework_id, name, parent_id, is_active, page_number, page_size
            config_data = await self.db_service.call_procedure(
                StoredProcedures.FILTER_FRAMEWORK_CONFIGURATIONS,
                [label_type_id, None, None, None, True, 1, 1]
            )

            if not config_data or not config_data[0]:
                # The provided label_id does not exist in configurations
                logger.warning(f"Label Type ID {label_type_id} not found in configurations.")
                return []

            # Assuming db_service returns a list of dictionaries
            current_config = config_data[0][0]
            parent_label_type_id = current_config.get("parent_id")

            # If there is no parent (it's a root node), return empty list
            if parent_label_type_id is None:
                return []

            # Step 2: Fetch nodes belonging to the found parent_label_type_id
            # mapping args: id, version_file_id, node_id, label_id, name, label_type, parent_node_id, is_active, page, size
            label_items_data = await self.db_service.call_procedure(
                StoredProcedures.FILTER_TEMP_NODES,
                [None, None, None, None, None, parent_label_type_id, None, True, 1, 5000]
            )

            if not label_items_data or not label_items_data[0]:
                return []

            # Defensive deduplication: prevent duplicate dropdown values when
            # repeated approve requests created repeated temp-node rows.
            deduped_items = []
            seen_keys = set()
            for item in label_items_data[0]:
                key = (item.get("node_id"), item.get("label_id"))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                deduped_items.append(item)

            return [NodeDetailsInDB(**item) for item in deduped_items]

        except Exception as e:
            logger.exception(f"Error retrieving parent label items for label_type_id {label_type_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
                detail="An error occurred while retrieving parent label items."
            )