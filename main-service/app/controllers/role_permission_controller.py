import logging
import json
from typing import List

from fastapi import HTTPException, status

from app.stored_procedures import StoredProcedures
from app.models.auth import RolePermissionUpdate, RolePermissionInDB, RolePermissionMultiCreate, RolePermissionSyncUpdate
from app.models.auth import PermissionInDB, PermissionResponse
from app.services.database_service import DatabaseService

logger = logging.getLogger(__name__)

class RolePermissionController:
    """
    Controller for managing role-permission assignments.

    This class encapsulates the business logic for creating, retrieving,
    updating, and deleting role-permission assignments, interacting with the database
    service to perform these actions.
    """

    def __init__(self, db_service: DatabaseService):
        self.db_service = db_service


    async def create_role_permissions(self, rp_data: RolePermissionMultiCreate, user_id: str) -> List[RolePermissionInDB]:
        """
        Creates multiple role-permission assignments for a single role.

        Args:
            rp_data (RolePermissionMultiCreate): The data for the new assignments.
            user_id (str): The ID of the user creating the assignments.

        Returns:
            List[RolePermissionInDB]: A list of the created assignments.

        Raises:
            HTTPException: If any of the assignments cannot be created.
        """
        try:
            # Validate role_id
            role_exists = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ROLES,
                [rp_data.role_id, None, True, 1, 1]
            )
            if not role_exists or not role_exists[0]:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Role with ID {rp_data.role_id} not found.")

            created_permissions = []
            for permission_id in rp_data.permission_ids:
                # Validate permission_id
                permission_exists = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_PERMISSIONS,
                    [permission_id, None, True, 1, 1]
                )
                if not permission_exists or not permission_exists[0]:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Permission with ID {permission_id} not found.")

                # Check if the role-permission mapping already exists and is active
                existing_mapping = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_ROLE_PERMISSIONS,
                    [None, rp_data.role_id, permission_id, True, 1, 1]
                )
                if existing_mapping and existing_mapping[0]:
                    # This is a choice: either skip or raise an error.
                    # For now, let's raise a conflict error.
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Role-permission mapping for role {rp_data.role_id} and permission {permission_id} already exists."
                    )

                results = await self.db_service.call_procedure(
                    StoredProcedures.UPSERT_ROLE_PERMISSION,
                    [
                        None,  # ID is auto-generated
                        rp_data.role_id,
                        permission_id,
                        True,  # is_active
                        user_id
                    ]
                )
                if not results or not results[0]:
                    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create role-permission assignment for permission ID {permission_id}.")
                
                created_rp_id = results[0][0]['id']
                created_rp_id = results[0][0]['id']
                new_assignments = await self.get_all_role_permissions(rp_id=created_rp_id, page_number=1, page_size=1)
                if new_assignments:
                    created_permissions.extend(new_assignments)
                            
            return created_permissions
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error creating multiple role-permission assignments: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")

    async def get_all_role_permissions(
        self, 
        rp_id: int = None,
        role_id: int = None,
        permission_id: int = None, 
        is_active: bool = None,
        page_number: int = None, 
        page_size: int = None,
        count_required: bool = False
    ) -> List[RolePermissionInDB]:
        """
        Retrieves role-permission assignments with pagination and optional filtering.

        Args:
            rp_id (int): Optional ID of the specific assignment.
            role_id (int): Optional Role ID filter.
            permission_id (int): Optional Permission ID filter.
            is_active (bool): Optional active status filter.
            page_number (int): The page number.
            page_size (int): The size of the page.
            count_required (bool): If True, returns dict with total_count and results.

        Returns:
            List[RolePermissionInDB] or dict: The data list or the paginated response.
        """
        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ROLE_PERMISSIONS,
                [
                    rp_id,          # id
                    role_id,        # role_id
                    permission_id,  # permission_id
                    is_active,      # is_active
                    page_number,
                    page_size
                ]
            )
            
            # Handle empty results
            if not results or not results[0]:
                return {"total_count": 0, "results": []} if count_required else []
            
            # Parse Data
            role_permissions_in_db = [RolePermissionInDB(**rp_data) for rp_data in results[0]]
            
            # Handle Count Requirement
            if count_required:
                if not results or len(results) < 2 or not results[1] or not results[1][0]:
                    return {"total_count": 0, "results": []}
                
                count = results[1][0]["total_count"]
                return {"total_count": count, "results": role_permissions_in_db}
                
            return role_permissions_in_db

        except Exception as e:
            logger.exception(f"Error retrieving role-permission assignments: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")

    async def get_permissions_by_role_id(
        self, 
        role_id: int, 
        page_number: int = None, 
        page_size: int = None
    )  -> PermissionResponse:
        """
        Retrieves actual Permission objects associated with a specific role ID.
        """
        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.GET_PERMISSIONS_BY_ROLE,
                [
                    role_id,
                    page_number,
                    page_size
                ]
            )
            
            # Handle empty results
            if not results or not results[0]:
                return {"total_count": 0, "results": []}

            # Map Data (To PermissionInDB, NOT RolePermissionInDB)
            permissions_in_db = [PermissionInDB(**perm_data) for perm_data in results[0]]
            
            # Handle Count
            if len(results) < 2 or not results[1] or not results[1][0]:
                 # Fallback
                 return {"total_count": len(permissions_in_db), "results": permissions_in_db}
            
            count = results[1][0]["total_count"]
            return {"total_count": count, "results": permissions_in_db}

        except Exception as e:
            logger.exception(f"Error retrieving permissions for role {role_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")

    async def update_role_permission(self, rp_id: str, rp_data: RolePermissionUpdate, user_id: str) -> RolePermissionInDB:
        """
        Updates an existing role-permission assignment.

        Args:
            rp_id (str): The ID of the assignment to update.
            rp_data (RolePermissionUpdate): The update data for the assignment.
            user_id (str): The ID of the user updating the assignment.

        Returns:
            RolePermissionInDB: The updated assignment's data.

        Raises:
            HTTPException: If the assignment is not found or cannot be updated.
        """
        existing_rp = await self.get_all_role_permissions(rp_id=rp_id, page_number=1, page_size=1)
        if not existing_rp or len(existing_rp) == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Role-permission assignment with ID {rp_id} not found."
            )

        # Ensure we have a valid existing_rp item to reference
        existing_rp_item = existing_rp[0]
        if not existing_rp_item:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Role-permission assignment with ID {rp_id} not found."
            )

        update_params = {
            "id": rp_id,
            "role_id": rp_data.role_id if rp_data.role_id is not None else existing_rp_item.role_id,
            "permission_id": rp_data.permission_id if rp_data.permission_id is not None else existing_rp_item.permission_id,
            "is_active": rp_data.is_active if rp_data.is_active is not None else existing_rp_item.is_active,
            "updated_by": user_id
        }

        try:
            # Determine the role_id and permission_id to be used for the update
            resolved_role_id = rp_data.role_id if rp_data.role_id is not None else existing_rp_item.role_id
            resolved_permission_id = rp_data.permission_id if rp_data.permission_id is not None else existing_rp_item.permission_id

            # Validate resolved_role_id
            role_exists = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ROLES,
                [resolved_role_id, None, True, 1, 1]
            )
            if not role_exists or not role_exists[0]:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Role with ID {resolved_role_id} not found.")

            # Validate resolved_permission_id
            permission_exists = await self.db_service.call_procedure(
                StoredProcedures.FILTER_PERMISSIONS,
                [resolved_permission_id, None, True, 1, 1]
            )
            if not permission_exists or not permission_exists[0]:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Permission with ID {resolved_permission_id} not found.")

            results = await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ROLE_PERMISSION,
                [
                    update_params["id"],
                    resolved_role_id,
                    resolved_permission_id,
                    update_params["is_active"],
                    update_params["updated_by"]
                ]
            )
            if not results or not results[0]:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update role-permission assignment.")
            
            # Fetch and return the updated role-permission assignment
            updated_rp = await self.get_all_role_permissions(rp_id=rp_id, page_number=1, page_size=1)
            if not updated_rp or len(updated_rp) == 0:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve updated role-permission assignment.")
            return updated_rp[0]
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error updating role-permission assignment {rp_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")

    async def replace_permissions_for_role(self, role_id: int, rp_data: RolePermissionSyncUpdate, user_id: str) -> List[RolePermissionInDB]:
        """
        Synchronizes the set of permissions for a role.

        Args:
            role_id (int): The ID of the role whose permissions are being synchronized.
            rp_data (RolePermissionSyncUpdate): The definitive list of active permission IDs for the role.
            user_id (str): The ID of the user performing the update.

        Returns:
            List[RolePermissionInDB]: The final list of active role-permission assignments for the role.
        """
        try:
            # Single DB call: the stored procedure validates inputs and synchronizes
            # role-permission mappings atomically in the database transaction.
            results = await self.db_service.call_procedure(
                StoredProcedures.REPLACE_ROLE_PERMISSIONS,
                [role_id, json.dumps(rp_data.permission_ids), user_id]
            )

            if not results or not results[0]:
                return []

            return [RolePermissionInDB(**rp_data) for rp_data in results[0]]

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error synchronizing role-permission assignments for role {role_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred during synchronization: {e}")

    async def delete_role_permission(self, rp_id: str, user_id: str) -> dict:
        """
        Deletes a role-permission assignment by its ID.

        Args:
            rp_id (str): The unique identifier of the assignment to delete.
            user_id (str): The ID of the user deleting the assignment.

        Returns:
            dict: A confirmation message.

        Raises:
            HTTPException: If the assignment is not found or cannot be deleted.
        """
        try:
            existing_rp = await self.get_all_role_permissions(rp_id=rp_id, page_number=1, page_size=1)
            if not existing_rp or len(existing_rp) == 0:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Role-permission assignment with ID {rp_id} not found."
                )

            await self.db_service.call_procedure(
                StoredProcedures.DELETE_ROLE_PERMISSION,
                [rp_id, user_id]
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error deleting role-permission assignment {rp_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")
