import logging
from typing import List

from fastapi import HTTPException, status

from app.stored_procedures import StoredProcedures
from app.models.org import OrganizationCreate, OrganizationUpdate, OrganizationInDB
from app.services.database_service import DatabaseService

logger = logging.getLogger(__name__)

class OrganizationController:
    """
    Controller for managing organization-related operations.

    This class encapsulates the business logic for creating, retrieving,
    updating, and deleting organizations, interacting with the database
    service to perform these actions.
    """

    def __init__(self, db_service: DatabaseService):
        self.db_service = db_service


    async def create_organization(self, org_data: OrganizationCreate, user_id: str) -> OrganizationInDB:
        """
        Creates a new organization and, optionally, its organizational units.

        Args:
            org_data (OrganizationCreate): The data for the new organization, including optional org_units.
            user_id (str): The ID of the user performing the action.

        Returns:
            OrganizationInDB: The created organization's data.

        Raises:
            HTTPException: If the organization cannot be created.
        """
        try:
            # Step 1: Create the organization
            results = await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ORGANIZATION,
                [None, org_data.name, org_data.description, True, user_id]
            )
            if not results or not results[0] or 'id' not in results[0][0]:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create organization: No ID returned.")
            
            new_org_id = results[0][0]['id']

            # Step 3: Construct the final response
            return await self.get_all_organizations(org_id = new_org_id, page_number=1, page_size=1)

        except Exception as e:
            logger.exception(f"Error creating organization: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")


    async def get_all_organizations(self, org_id: int = None, page_number: int = None, page_size: int = None, is_active: bool = None, count_required: bool = False) -> List[OrganizationInDB]:
        """
        Retrieves all organizations with pagination, without their associated organizational units.

        Args:
            page_number (int): The page number to retrieve.
            page_size (int): The number of organizations per page.

        Returns:
            List[OrganizationInDB]: A list of all organizations.
        """
        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ORGANIZATIONS,
                [
                    org_id,     # id
                    None,       # name
                    is_active,  # is_active
                    page_number,
                    page_size
                ]
            )
            if not results or not results[0]:
                return {"total_count": 0, "results": []} if count_required else []
            
            organizations_in_db = [OrganizationInDB(**org_data) for org_data in results[0]]
            
            if count_required:
                if not results or not results[1] or not results[1][0]:
                    return {"total_count": 0, "results": []}
                count = results[1][0]["total_count"]
                return {"total_count": count, "results": organizations_in_db}
            return organizations_in_db
        except Exception as e:
            logger.exception(f"Error retrieving all organizations: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")


    async def get_organization_by_id(self, org_id: int) -> OrganizationInDB:
        """
        Retrieves a single organization by its ID.

        Args:
            org_id (int): The ID of the organization to retrieve.

        Returns:
            OrganizationInDB: The organization data.

        Raises:
            HTTPException: If the organization is not found.
        """
        try:
            organizations = await self.get_all_organizations(org_id=org_id, page_number=1, page_size=1)
            if not organizations:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Organization with ID {org_id} not found.")
            return organizations[0]
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error retrieving organization {org_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")


    async def update_organization(self, org_id: int, org_data: OrganizationUpdate, user_id: str) -> OrganizationInDB:
        """
        Updates an existing organization and its organizational units.

        Args:
            org_id (int): The ID of the organization to update.
            org_data (OrganizationUpdate): The update data for the organization, including optional org_units.
            updated_by (str): The ID of the user performing the action.

        Returns:
            OrganizationInDB: The updated organization's data.

        Raises:
            HTTPException: If the organization is not found or cannot be updated.
        """
        # Step 1: Fetch existing organization to merge updates and ensure it exists
        existing_org = await self.get_all_organizations(org_id = org_id, page_number=1, page_size=1)
        
        update_params = {
            "id": org_id,
            "name": org_data.name if org_data.name is not None else existing_org[0].name,
            "description": org_data.description if org_data.description is not None else existing_org[0].description,
            "is_active": existing_org[0].is_active,
            "updated_by": user_id
        }

        try:
            # Step 2: Update the organization details
            await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ORGANIZATION,
                list(update_params.values())
            )

            # Step 4: Fetch the final state of the organization
            return await self.get_all_organizations(org_id = org_id, page_number=1, page_size=1)

        except HTTPException:
            raise  # Re-raise 404 from get_organization_by_id
        except Exception as e:
            logger.exception(f"Error updating organization {org_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")


    async def delete_organization(self, org_id: int, user_id: str) -> dict:
        """
        Deletes an organization by its ID.

        Args:
            org_id (str): The unique identifier of the organization to delete.

        Returns:
            dict: A confirmation message.

        Raises:
            HTTPException: If the organization is not found or cannot be deleted.
        """
        try:
            # First, check if the organization exists
            await self.get_all_organizations(org_id = org_id, page_number=1, page_size=1) # This will raise 404 if not found

            await self.db_service.call_procedure(
                StoredProcedures.DELETE_ORGANIZATION,
                [org_id, user_id]
            )

            return {"message": "Organization deleted successfully."}
        except HTTPException:
            raise # Re-raise 404 from get_organization_by_id
        except Exception as e:
            logger.exception(f"Error deleting organization {org_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")
