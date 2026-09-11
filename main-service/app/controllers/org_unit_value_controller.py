import logging
from typing import List, Optional

from fastapi import HTTPException, status

from app.core.stored_procedures import StoredProcedures
from app.models.org import OrgUnitValueHierarchy, OrgUnitValueCreatePayload, OrgUnitValueInDB, OrgUnitValueResponse, OrgUnitValueUpdatePayload, OrgUnitValuesResponse
from app.services.database_service import DatabaseService

logger = logging.getLogger(__name__)

class OrgUnitValueController:
    """
    Controller for managing organizational unit value-related operations.
    """

    def __init__(self, db_service: DatabaseService):
        self.db_service = db_service


    async def _insert_hierarchy(self, data: OrgUnitValueHierarchy, parent_id: Optional[str], user_id: str) -> None:
        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ORG_UNIT_VALUE,
                [
                    None,  # id
                    data.org_unit_id,
                    parent_id,
                    data.value,
                    True,  # is_active
                    user_id
                ]
            )
            if not results or not results[0] or 'id' not in results[0][0]:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create organizational unit value: No ID returned.")
            
            new_org_unit_value_id = results[0][0]['id']

            if data.children and len(data.children)>0:
                for child in data.children:
                    await self._insert_hierarchy(child, new_org_unit_value_id, user_id)
        except:
            raise

    
    async def _get_hierarchy(self, data: dict, root: int) -> OrgUnitValueResponse:
        try:
            response = OrgUnitValueResponse(
                id = data[root].get("id"),
                org_unit_id= data[root].get("org_unit_id"),
                org_unit_name= data[root].get("org_unit_name"),
                value = data[root].get("value"),
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
        

    async def create_org_unit_values(self, org_unit_value_data: OrgUnitValueCreatePayload, user_id: str) -> OrgUnitValuesResponse:
        """
        Creates new organizational unit values.
        """
        try:
            await self._insert_hierarchy(org_unit_value_data.hierarchy, None, user_id)
            return await self.get_org_unit_values_by_org_id(org_unit_value_data.organization_id)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error creating organizational unit: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")
        
    
    async def get_org_unit_value_by_id(self, org_unit_value_id: int) -> List[OrgUnitValueInDB]:
        """
        Retrieve organization unit value for a given ID.
        """
        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ORG_UNIT_VALUES,
                [
                    org_unit_value_id,      # id
                    None,                   # organization_id
                    None,                   # org_unit_id
                    None,                   # parent_org_unit_value_id
                    None,                   # value
                    True,                   # is_active
                    1,                      # page_number (get all)
                    1000                    # page_size (get all, adjust as needed)
                ]
            )
            if not results or not results[0]:
                return []
            
            org_unit_values_in_db = [OrgUnitValueInDB(**data) for data in results[0]]
            
            return org_unit_values_in_db

        except Exception as e:
            logger.exception(f"Error retrieving organizational unit value for ID {org_unit_value_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")
        
    
    async def get_org_unit_values_by_org_id_by_org_unit(self, org_id: int, org_unit_id: int) -> List[OrgUnitValueInDB]:
        """
        Retrieve organization unit value for a given organization unit ID.
        """
        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ORG_UNIT_VALUES,
                [
                    None,                   # id
                    org_id,                 # organization_id
                    org_unit_id,            # org_unit_id
                    None,                   # parent_org_unit_value_id
                    None,                   # value
                    True,                   # is_active
                    1,                      # page_number (get all)
                    1000                    # page_size (get all, adjust as needed)
                ]
            )
            if not results or not results[0]:
                return []
            
            org_unit_values_in_db = [OrgUnitValueInDB(**data) for data in results[0]]
            
            return org_unit_values_in_db

        except Exception as e:
            logger.exception(f"Error retrieving organizational ID {org_id} and organization unit ID {org_unit_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")

    
    async def get_org_unit_values_by_org_id(self, organization_id: int) -> OrgUnitValuesResponse:
        """
        Retrieves all organization unit values for a given organization.
        """
        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ORG_UNIT_VALUES,
                [
                    None,                   # id
                    organization_id,        # organization_id
                    None,                   # org_unit_id
                    None,                   # parent_org_unit_value_id
                    None,                   # value
                    True,                   # is_active
                    1,                      # page_number (get all)
                    1000                    # page_size (get all, adjust as needed)
                ]
            )
            if not results or not results[0]:
                return []
            
            data = {}
            root_list = []
            for result in results[0]:
                if not result.get("parent_org_unit_value_id"):
                    root_list.append(result.get("id"))
                result["children"]=[]
                data[result.get("id")]=result

            for result in results[0]:
                if result.get("parent_org_unit_value_id"):
                    data[result.get("parent_org_unit_value_id")]["children"].append(result.get("id"))

            response = await self._get_hierarchy(data, int(root_list[0]))

            return OrgUnitValuesResponse(
                organization_id = organization_id,
                hierarchy = response
            )

        except Exception as e:
            logger.exception(f"Error retrieving organizational unit values for organization {organization_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")


    async def update_org_unit_value(self, org_unit_value_id: int, org_unit_value_data: OrgUnitValueUpdatePayload, user_id: str) -> OrgUnitValuesResponse:
        """
        Updates an existing organizational unit value.
        """
        try:
            results = await self.get_org_unit_value_by_id(org_unit_value_id) # Check if exists

            await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ORG_UNIT_VALUE,
                [
                    org_unit_value_id,  # id
                    None,
                    None,
                    org_unit_value_data.value,
                    True,  # is_active
                    user_id
                ]
            )

            if not results or not results[0]:
                return None
            
            org_id = results[0].organization_id

            return await self.get_org_unit_values_by_org_id(org_id) # Check if exists
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error updating organization unit {org_unit_value_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")


    async def delete_org_unit_values_by_org_id(self, org_id: int, user_id: str) -> dict:
        try:
            await self.db_service.call_procedure(
                StoredProcedures.DELETE_ORG_UNIT_VALUES,
                [org_id, user_id]
            )
            return {"message": "Organization unit values deleted successfully."}
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error deleting organization unit values for organization: {org_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")
        