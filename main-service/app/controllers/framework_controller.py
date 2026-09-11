import logging


from typing import Optional, List, Union, Dict, Any


from fastapi import HTTPException, status

from app.stored_procedures import StoredProcedures
from app.models.framework import FrameworkCreate, FrameworkUpdate, FrameworkInDB
from app.models.files import VersionInDB
from app.models.files import FileInDB, FileType
from app.services.database_service import DatabaseService

logger = logging.getLogger(__name__)


class FrameworkController:
    """
    Controller for managing framework-related operations.

    This class encapsulates the business logic for creating, retrieving,
    updating, and deleting frameworks, interacting with the database
    service to perform these actions.
    """

    def __init__(self, db_service: DatabaseService):
        """
        Initializes the FrameworkController.

        Args:
            db_service (DatabaseService): The database service instance.
        """
        self.db_service = db_service

    async def create_framework(self, framework_data: FrameworkCreate, user_id: str) -> FrameworkInDB:
        """
        Creates a new framework and its associated configurations.

        Args:
            framework_data (FrameworkCreate): The data for the new framework.
            user_id (str): The ID of the user creating the framework.

        Returns:
            FrameworkInDB: The created framework's data.

        Raises:
            HTTPException: If the framework cannot be created or configurations are invalid.
        """
        try:
            framework_result = await self.db_service.call_procedure(
                StoredProcedures.UPSERT_FRAMEWORK,
                [None, framework_data.name, framework_data.description, True, user_id]
            )
            if not framework_result or not framework_result[0] or 'id' not in framework_result[0][0]:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create framework.")

            framework_id = framework_result[0][0]['id']

            # Create default version 1 for the new framework, linked to the change record
            version_results = await self.db_service.call_procedure(
                StoredProcedures.UPSERT_VERSION,
                [None, framework_id, 1, True, True, user_id] # id, framework_id, number, is_active, user_id
            )
            if not version_results or not version_results[0] or 'id' not in version_results[0][0]:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create version for framework.")
            version_id = version_results[0][0]['id']

            # Create a change request for the new framework
            change_request_results = await self.db_service.call_procedure(
                StoredProcedures.UPSERT_CHANGE_REQUEST,
                [
                    None, # ID is auto-generated
                    version_id,
                    "open", # Default status for new change request
                    True,
                    user_id
                ]
            )
            if not change_request_results or not change_request_results[0] or 'id' not in change_request_results[0][0]:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create change request for framework.")
            
            response = await self.get_frameworks(framework_id)

            return response[0]

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error creating framework: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred while creating the framework: {e}")


    async def get_frameworks(self, framework_id: Optional[int] = None, page_number: int = None, page_size: int = None, count_required: bool = False) -> List[FrameworkInDB]:
        """
        Retrieves frameworks with pagination.

        Args:
            page_number (int): The page number to retrieve.
            page_size (int): The number of frameworks per page.

        Returns:
            List[FrameworkInDB]: A list of frameworks.
        """
        try:
            # The procedure returns two result sets: data and total count.
            results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_FRAMEWORKS,
                [framework_id, None, True, page_number, page_size]
            )
            if not results or not results[0]:
                return {"total_count": 0, "results": []} if count_required else []
            
            frameworks = [FrameworkInDB(**row) for row in results[0]]

            if count_required:
                if not results or not results[1] or not results[1][0]:
                    return {"total_count": 0, "results": []}
                count = results[1][0]["total_count"]
                return {"total_count": count, "results": frameworks}
            return frameworks
        except Exception as e:
            logger.exception(f"Error retrieving all frameworks: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred while retrieving frameworks: {e}")
        

    
    async def get_frameworks_by_organization(
            self,
            email: str,
            framework_id: Optional[int] = None,
            page_number: Optional[int] = None,
            page_size: Optional[int] = None,
            count_required: bool = True,
        ) -> Union[List[FrameworkInDB], Dict[str, Any]]:
            """
            Retrieves frameworks for the user's organization with pagination.
            Mirrors the behavior and return shape of `get_frameworks`.

            Args:
                email (str): User's email to resolve organization_id.
                framework_id (Optional[int]): Specific framework to filter by.
                page_number (Optional[int]): Page number for pagination.
                page_size (Optional[int]): Page size for pagination.
                count_required (bool): If True, returns {"total_count": int, "results": List[FrameworkInDB]}.

            Returns:
                Union[List[FrameworkInDB], Dict[str, Any]]: Frameworks list or paged result when count_required=True.
            """
            try:
                # 1) Resolve organization_id from email
                users = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_USERS,
                    [None, None, None, email, None, True, 1, 1]  # fetch only one
                )


                # users is expected to be a list of result-sets -> users[0] is the first set (rows)
                if not users or not users[0] or not users[0][0]:
                    # No user found for email; stay consistent with get_frameworks' empty handling
                    if count_required:
                        return {"total_count": 0, "results": []}
                    return []

                user_row = users[0][0]

                org_id = user_row.get("organization_id")

                if org_id is None:
                    if count_required:
                        return {"total_count": 0, "results": []}
                    return []

                # 2) Call org-scoped frameworks filter
                # Expecting two result sets like get_frameworks:
                #   results[0] -> data rows
                #   results[1] -> [{"total_count": N}] when count_required is True (SP should return it)
                results = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_FRAMEWORKS_WITH_ORGANIZATION,
                    [framework_id, None, org_id, True, page_number, page_size]
                )


                if not results or not results[0]:
                    # No rows
                    if count_required:
                        return {"total_count": 0, "results": []}
                    return []

                frameworks = [FrameworkInDB(**row) for row in results[0]]

                if count_required:
                    # Safely read total count from the second result set
                    if len(results) < 2 or not results[1] or not results[1][0] or "total_records" not in results[1][0]:
                        # Fallback to 0 if SP didn't return count (or adjust per your SP guarantees)
                        return {"total_count": 0, "results": frameworks}
                    count = results[1][0]["total_records"]
                    return {"total_count": count, "results": frameworks}

                return frameworks

            except Exception as e:
                logger.exception(f"Error retrieving frameworks by organization for email={email}: {e}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"An error occurred while retrieving frameworks by organization: {e}"
                )



    async def update_framework(self, framework_id: int, framework_data: FrameworkUpdate, user_id: str) -> FrameworkInDB:
        """
        Updates an existing framework and its associated configurations.

        Args:
            framework_id (int): The ID of the framework to update.
            framework_data (FrameworkUpdate): The update data for the framework.
            user_id (str): The ID of the user performing the update.

        Returns:
            FrameworkInDB: The updated framework's data.

        Raises:
            HTTPException: If the framework is not found or cannot be updated.
        """
        try:
            # Ensure the framework exists before attempting to update
            existing_frameworks = await self.get_frameworks(framework_id)
            existing_framework = existing_frameworks[0]

            update_data = framework_data.model_dump(exclude_unset=True)

            # Update framework basic details
            results = await self.db_service.call_procedure(
                StoredProcedures.UPSERT_FRAMEWORK,
                [
                    framework_id,
                    update_data.get('name', existing_framework.name),
                    update_data.get('description', existing_framework.description), # Add description here
                    existing_framework.is_active,
                    user_id
                ]
            )
            if not results or not results[0] or 'id' not in results[0][0]:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update framework.")
            
            response = await self.get_frameworks(framework_id)

            return response[0]
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error updating framework {framework_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred while updating the framework: {e}")


    async def delete_framework(self, framework_id: int, user_id: str) -> None:
        """
        Deletes a framework by its ID by marking it as inactive.

        Args:
            framework_id (int): The unique identifier of the framework to delete.
            user_id (str): The ID of the user performing the deletion.

        Raises:
            HTTPException: If the framework is not found or cannot be deleted.
        """
        try:
            # Ensure the framework exists before attempting to delete
            await self.get_frameworks(framework_id)

            await self.db_service.call_procedure(
                StoredProcedures.DELETE_FRAMEWORK,
                [framework_id, user_id]
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error deleting framework {framework_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred while deleting the framework: {e}")
    

    async def get_versions_by_framework_id(self, framework_id: int, page_number: int = None, page_size: int = None, count_required: bool = False) -> List[VersionInDB]:
        """
        Retrieves versions of framework with pagination.

        Args:
            page_number (int): The page number to retrieve.
            page_size (int): The number of frameworks per page.

        Returns:
            List[VersionInDB]: A list of versions.
        """
        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_VERSIONS,
                [None, framework_id, None, None, True, page_number, page_size]
            )
            if not results or not results[0]:
                return {"total_count": 0, "results": []} if count_required else []
            
            versions = [VersionInDB(**row) for row in results[0]]
            
            if count_required:
                if not results or not results[1] or not results[1][0]:
                    return {"total_count": 0, "results": []}
                count = results[1][0]["total_count"]
                return {"total_count": count, "results": versions}
            return versions
        except Exception as e:
            logger.exception(f"Error retrieving all versions: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred while retrieving versions: {e}")


    async def get_files_by_framework_id(self, framework_id: int) -> List[FileInDB]:
        """
        Retrieves files of framework.

        Args:
            framework_id (int): The framework ID

        Returns:
            List[FileResponse]: A list containing only the latest framework file.
        """
        try:
            # Keep version selection consistent with FileController.link_file_to_entity,
            # which links uploaded files to the latest active version returned first.
            versions = await self.db_service.call_procedure(
                StoredProcedures.FILTER_VERSIONS,
                [None, framework_id, None, None, True, 1, 1]
            )
            if not versions or not versions[0]:
                return []
            
            version_id = versions[0][0]["id"]

            version_file_details = await self.db_service.call_procedure(
                StoredProcedures.FILTER_VERSION_FILES,
                [None, version_id, None, True, 1, 1000000]
            )
            if not version_file_details or not version_file_details[0]:
                return []
            
            response = []
            for version_file in version_file_details[0]:
                file_details = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_FILES,
                    [version_file.get("file_id"), None, None, None, True, 1, 1]
                )
                if not file_details or not file_details[0]:
                    continue

                file_record = FileInDB(**file_details[0][0])
                if file_record.file_type == FileType.FRAMEWORK:
                    response.append(file_record)

            if not response:
                return []

            latest_file = max(
                response,
                key=lambda f: (f.created_on, f.id),
            )
            return [latest_file]

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error fetching files for framework ID - {framework_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")
        

    async def framework_version_in_use(self, framework_id: int, version_id: int, user_id: str) -> List[VersionInDB]:
        """
        Make version in use for the framework.

        Args:
            framework_id (int): Unique identifier for framework.
            version_id (int): Unique identifier for framework version.
            page_number (int): The page number to retrieve.
            page_size (int): The number of frameworks per page.

        Returns:
            List[VersionInDB]: A list of versions.
        """
        try:
            in_use_versions = await self.db_service.call_procedure(
                StoredProcedures.FILTER_VERSIONS,
                [None, framework_id, None, True, True, 1, 1000000]
            )

            for in_use_version in in_use_versions[0]:
                await self.db_service.call_procedure(
                    StoredProcedures.UPSERT_VERSION,
                    [in_use_version.get("id"), None, None, False, True, user_id] # id, framework_id, number, is_active, user_id
                )

            await self.db_service.call_procedure(
                StoredProcedures.UPSERT_VERSION,
                [version_id, None, None, True, True, user_id] # id, framework_id, number, is_active, user_id
            )
            
            return await self.get_versions_by_framework_id(framework_id, 1, 1000000)
        except Exception as e:
            logger.exception(f"Error retrieving all frameworks: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred while retrieving frameworks: {e}")
