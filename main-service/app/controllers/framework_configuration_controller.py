import logging
from typing import List, Optional

from fastapi import HTTPException, status

from app.core.stored_procedures import StoredProcedures
from app.models.framework import Hierarchy, FrameworkConfigurationCreatePayload, FrameworkConfigurationInDB, FrameworkConfigurationResponse, FrameworkConfigurationUpdatePayload, FrameworkConfigurationsResponse
from app.services.database_service import DatabaseService

logger = logging.getLogger(__name__)

class FrameworkConfigurationController:
    """
    Controller for managing framework configuration-related operations.
    """

    def __init__(self, db_service: DatabaseService):
        self.db_service = db_service


    async def _insert_hierarchy(self, framework_id: str, data: Hierarchy, parent_id: Optional[str], user_id: str) -> None:
        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.UPSERT_FRAMEWORK_CONFIGURATION,
                [
                    None,  # id
                    framework_id,
                    data.name,
                    data.description,
                    parent_id, # Use parent_id directly from the model
                    True,  # is_active
                    user_id
                ]
            )
            if not results or not results[0] or 'id' not in results[0][0]:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create framework configuration: No ID returned.")
            
            new_framework_configuration_id = results[0][0]['id']

            if data.children and len(data.children)>0:
                for child in data.children:
                    await self._insert_hierarchy(framework_id, child, new_framework_configuration_id, user_id)
        except:
            raise

    
    async def _get_hierarchy(self, data: dict, root: int) -> FrameworkConfigurationResponse:
        try:
            response = FrameworkConfigurationResponse(
                id = data[root].get("id"),
                name = data[root].get("name"),
                description = data[root].get("description"),
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
        

    async def create_framework_configuration(self, framework_configuration_data: FrameworkConfigurationCreatePayload, user_id: str) -> FrameworkConfigurationsResponse:
        """
        Creates new framework configuration.
        """
        try:
            await self._insert_hierarchy(framework_configuration_data.framework_id, framework_configuration_data.hierarchy, None, user_id)
            return await self.get_framework_configuration_by_framework_id(framework_configuration_data.framework_id)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error creating framework configuration: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")
        
    
    async def get_framework_configuration_by_id(self, framework_configuration_id: int) -> List[FrameworkConfigurationInDB]:
        """
        Retrieve framework configuration for a given ID.
        """
        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_FRAMEWORK_CONFIGURATIONS,
                [
                    framework_configuration_id,     # id
                    None,                           # framework_id
                    None,                           # name
                    None,                           # parent_id (to get all)
                    True,                           # is_active
                    1,                              # page_number (get all)
                    1000                            # page_size (get all, adjust as needed)
                ]
            )
            if not results or not results[0]:
                return []
            
            framework_configuration_in_db = [FrameworkConfigurationInDB(**data) for data in results[0]]
            
            return framework_configuration_in_db

        except Exception as e:
            logger.exception(f"Error retrieving framework configuration for ID {framework_configuration_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")

    
    async def get_framework_configuration_by_framework_id(self, framework_id: int) -> FrameworkConfigurationsResponse:
        """
        Retrieves all framework_configuration for a given framework.
        """
        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_FRAMEWORK_CONFIGURATIONS,
                [
                    None,                           # id
                    framework_id,                   # framework_id
                    None,                           # name
                    None,                           # parent_id (to get all)
                    True,                           # is_active
                    1,                              # page_number (get all)
                    1000                            # page_size (get all, adjust as needed)
                ]
            )
            if not results or not results[0]:
                return []
            
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

            return FrameworkConfigurationsResponse(
                framework_id= framework_id,
                hierarchy = response
            )

        except Exception as e:
            logger.exception(f"Error retrieving framework configuration for framework {framework_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")


    async def update_framework_configuration(self, framework_configuration_id: int, framework_configuration_data: FrameworkConfigurationUpdatePayload, user_id: str) -> FrameworkConfigurationsResponse:
        """
        Updates an existing framework configuration.
        """
        try:
            results = await self.get_framework_configuration_by_id(framework_configuration_id) # Check if exists

            framework_results = await self.db_service.call_procedure(
                StoredProcedures.UPSERT_FRAMEWORK_CONFIGURATION,
                [
                    framework_configuration_id,  # id
                    None,
                    framework_configuration_data.name,
                    framework_configuration_data.description,
                    None,
                    True,  # is_active
                    user_id
                ]
            )

            if not framework_results or not framework_results[0]:
                return None
            
            framework_id = results[0].framework_id

            return await self.get_framework_configuration_by_framework_id(framework_id) # Check if exists
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error updating framework configuration {framework_configuration_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")
        