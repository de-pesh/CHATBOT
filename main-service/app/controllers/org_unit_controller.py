import logging
from typing import List, Optional

from fastapi import HTTPException, status

from app.core.stored_procedures import StoredProcedures
from app.models.org import OrgUnitHierarchy, OrgUnitCreatePayload, OrgUnitInDB, OrgUnitResponse, OrgUnitUpdatePayload, OrgUnitsResponse
from app.services.database_service import DatabaseService

logger = logging.getLogger(__name__)

class OrgUnitController:
    """
    Controller for managing organizational unit-related operations.
    """

    def __init__(self, db_service: DatabaseService):
        self.db_service = db_service


    async def _insert_hierarchy(self, org_id: str, data: OrgUnitHierarchy, parent_id: Optional[str], user_id: str) -> None:
        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ORG_UNIT,
                [
                    None,  # id
                    org_id,
                    data.name,
                    parent_id, # Use parent_id directly from the model
                    True,  # is_active
                    user_id
                ]
            )
            if not results or not results[0] or 'id' not in results[0][0]:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create organizational unit: No ID returned.")
            
            new_org_unit_id = results[0][0]['id']

            if data.children and len(data.children)>0:
                for child in data.children:
                    await self._insert_hierarchy(org_id, child, new_org_unit_id, user_id)
        except:
            raise

    
    async def _get_hierarchy(self, data: dict, root: int) -> OrgUnitResponse:
        try:
            response = OrgUnitResponse(
                id = data[root].get("id"),
                name = data[root].get("name"),
                is_active = data[root].get("is_active"),
                created_by = data[root].get("created_by"),
                created_on = data[root].get("created_on"),
                updated_by = data[root].get("updated_by"),
                updated_on = data[root].get("updated_on"),
                children = [await self._get_hierarchy(data, int(child)) for child in data[root].get("children")]
            )
            return response
        except:
            raise
        

    async def create_org_units(self, org_unit_data: OrgUnitCreatePayload, user_id: str) -> OrgUnitsResponse:
        """
        Creates new organizational units.
        """
        try:
            await self._insert_hierarchy(org_unit_data.organization_id, org_unit_data.hierarchy, None, user_id)
            return await self.get_org_units_by_org_id(org_unit_data.organization_id)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error creating organizational units: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")
        
    
    async def get_org_unit_by_id(self, org_unit_id: int) -> List[OrgUnitInDB]:
        """
        Retrieve organization unit for a given ID.
        """
        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ORG_UNITS,
                [
                    org_unit_id,     # id
                    None,            # organization_id
                    None,            # name
                    None,            # parent_id (to get all)
                    True,            # is_active
                    1,               # page_number (get all)
                    1000             # page_size (get all, adjust as needed)
                ]
            )
            if not results or not results[0]:
                return []
            
            org_units_in_db = [OrgUnitInDB(**data) for data in results[0]]
            
            return org_units_in_db

        except Exception as e:
            logger.exception(f"Error retrieving organizational unit for ID {org_unit_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")

    
    async def get_org_units_by_org_id(self, organization_id: int) -> OrgUnitsResponse:
        """
        Retrieves all organization units for a given organization.
        """
        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ORG_UNITS,
                [
                    None,            # id
                    organization_id, # organization_id
                    None,            # name
                    None,            # parent_id (to get all)
                    True,            # is_active
                    1,               # page_number (get all)
                    1000             # page_size (get all, adjust as needed)
                ]
            )
            if not results or not results[0]:
                return OrgUnitsResponse(
                    organization_id = organization_id
                )
            
            data = {}
            root_list = []
            for result in results[0]:
                if not result.get("parent_id"):
                    root_list.append(result.get("id"))
                result["children"]=[]
                data[result.get("id")]=result
                
            for result in results[0]:
                if result.get("parent_id"):
                    data[result.get("parent_id")]["children"].append(result.get("id"))

            response = await self._get_hierarchy(data, int(root_list[0]))

            return OrgUnitsResponse(
                organization_id = organization_id,
                hierarchy = response
            )

        except Exception as e:
            logger.exception(f"Error retrieving organizational units for organization {organization_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")


    async def update_org_unit(self, org_unit_id: int, org_unit_data: OrgUnitUpdatePayload, user_id: str) -> OrgUnitsResponse:
        """
        Updates an existing organizational unit.
        """
        try:
            results = await self.get_org_unit_by_id(org_unit_id) # Check if exists

            await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ORG_UNIT,
                [
                    org_unit_id,        # id
                    None,               # organization_id
                    org_unit_data.name, # name
                    None,               # parent_id (to get all)
                    None,               # is_active
                    user_id                # page_size (get all, adjust as needed)
                ]
            )

            if not results or not results[0]:
                return None
            
            org_id = results[0].organization_id

            return await self.get_org_units_by_org_id(org_id) # Check if exists
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error updating organization unit {org_unit_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")
        
    
    async def delete_org_units_by_org_id(self, org_id: int, user_id: str) -> dict:
        try:
            await self.db_service.call_procedure(
                StoredProcedures.DELETE_ORG_UNITS,
                [org_id, user_id]
            )
            return {"message": "Organization units deleted successfully."}
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error deleting organization units for organization: {org_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")
        