import logging
from typing import List

from fastapi import HTTPException, status

from app.core.stored_procedures import StoredProcedures
from app.models.org import OrgActionerHierarchy, OrgActionerCreate, OrgActionerHierarchyResponse, OrgActionerInDB, OrgActionerUpdate, OrgActionersResponse, OrgGateSettingActionersResponse
from app.services.database_service import DatabaseService

logger = logging.getLogger(__name__)

class OrgActionerController:
    """
    Controller for managing organizational actioner-related operations.
    """

    def __init__(self, db_service: DatabaseService):
        self.db_service = db_service


    def _flatten_actioners(self, data: List[OrgActionerHierarchy]) -> List[OrgActionerHierarchy]:
        flattened: List[OrgActionerHierarchy] = []
        for actioner in data:
            flattened.append(
                OrgActionerHierarchy(
                    email=actioner.email,
                    is_reviewer=actioner.is_reviewer,
                    is_approver=actioner.is_approver,
                    next_actioner=[],
                )
            )
            if actioner.next_actioner:
                flattened.extend(self._flatten_actioners(actioner.next_actioner))
        return flattened


    async def _insert_hierarchy(self, org_gate_setting_id: str, data: List[OrgActionerHierarchy], user_id: str) -> List[int]:
        try:
            response = []
            flattened_actioners = self._flatten_actioners(data)
            for actioner in flattened_actioners:
                results = await self.db_service.call_procedure(
                    StoredProcedures.UPSERT_ORG_ACTIONER,
                    [
                        None,                 # id
                        org_gate_setting_id,  # org_settings_id
                        actioner.email,       # email
                        actioner.is_reviewer, # is_reviewer
                        actioner.is_approver, # is_approver
                        None,                 # reserved argument
                        True,                 # is_active
                        user_id               # user_id
                    ]
                )
                if not results or not results[0] or 'id' not in results[0][0]:
                    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create organizational actioner: No ID returned.")

                response.append(results[0][0]['id'])

            return response
        except:
            raise
        

    async def create_org_actioners(self, org_actioner_data: OrgActionerCreate, user_id: str) -> OrgGateSettingActionersResponse:
        """
        Creates new organizational actioners.
        """
        try:
            await self._insert_hierarchy(org_actioner_data.org_gate_setting_id, org_actioner_data.actioner_hierarchy, user_id)
            return await self.get_org_actioners_by_org_gate_setting_id(org_actioner_data.org_gate_setting_id)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error creating organizational units: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")
        
    
    async def get_org_actioner_by_id(self, org_actioner_id: int) -> List[OrgActionerInDB]:
        """
        Retrieve organization actioner for a given ID.
        """
        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ORG_ACTIONER,
                [
                    org_actioner_id, # id
                    None,            # organization_id
                    None,            # org_gate_setting_id
                    None,            # is_reviewer
                    None,            # is_approver
                    True,            # is_active
                    1,               # page_number (get all)
                    1000             # page_size (get all, adjust as needed)
                ]
            )
            if not results or not results[0]:
                return []
            
            org_actioners_in_db = [OrgActionerInDB(**data) for data in results[0]]
            
            return org_actioners_in_db

        except Exception as e:
            logger.exception(f"Error retrieving organizational actioners for ID {org_actioner_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")

    
    async def get_org_actioners_by_org_gate_setting_id(self, org_gate_setting_id: int) -> OrgGateSettingActionersResponse:
        """
        Retrieves all organization actioners for a given organization gate setting.
        """
        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ORG_ACTIONER,
                [
                    None,                   # id
                    None,                   # organization_id
                    org_gate_setting_id,    # org_gate_setting_id (a.k.a. org_settings_id)
                    None,                   # is_reviewer
                    None,                   # is_approver
                    True,                   # is_active
                    1,                      # page_number (get all)
                    1000                    # page_size (get all, adjust as needed)
                ]
            )
            if not results or not results[0]:
                # No actioners configured yet for this gate setting; return an empty
                # but well-formed response so callers don't break on shape.
                return OrgGateSettingActionersResponse(
                    org_gate_setting_id=org_gate_setting_id,
                    actioner_hierarchy=[],
                )

            response_list = [
                OrgActionerHierarchyResponse(
                    id=result.get("id"),
                    email=result.get("email"),
                    is_reviewer=result.get("is_reviewer"),
                    is_approver=result.get("is_approver"),
                    is_active=result.get("is_active"),
                    created_by=result.get("created_by"),
                    created_on=result.get("created_on"),
                    updated_by=result.get("updated_by"),
                    updated_on=result.get("updated_on"),
                    next_actioner=[],
                )
                for result in results[0]
            ]

            return OrgGateSettingActionersResponse(
                org_gate_setting_id = org_gate_setting_id,
                actioner_hierarchy = response_list
            )

        except Exception as e:
            logger.exception(f"Error retrieving organizational actioners for organization gate setting ID {org_gate_setting_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")


    async def update_org_actioner(self, org_actioner_id: int, org_actioner_data: OrgActionerUpdate, user_id: str) -> OrgGateSettingActionersResponse:
        """
        Updates an existing organizational actioner.
        """
        try:
            results = await self.get_org_actioner_by_id(org_actioner_id) # Check if exists

            await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ORG_ACTIONER,
                [
                    org_actioner_id,            # id
                    None,                       # org_gate_setting_id
                    org_actioner_data.email,    # email
                    None,                       # is_reviewer
                    None,                       # is_approver
                    None,                       # reserved argument
                    None,                       # is_active
                    user_id                     # user_id
                ]
            )

            if not results or not results[0]:
                return None
            
            org_gate_setting_id = results[0].org_gate_setting_id

            return await self.get_org_actioners_by_org_gate_setting_id(org_gate_setting_id)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error updating organization actioner {org_actioner_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")


    async def delete_org_actioners_by_org_id(self, org_id: int, user_id: str) -> dict:
        try:
            await self.db_service.call_procedure(
                StoredProcedures.DELETE_ORG_ACTIONER,
                [org_id, user_id]
            )
            return {"message": "Organization actioners deleted successfully."}
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error deleting organization actioners for organization: {org_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")
        
    
    async def get_org_actioners_by_org_id(self, org_id: int) -> OrgActionersResponse:
        """
        Retrieves all organization actioners for a given organization.
        """
        try:
            org_settings = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ORG_SETTINGS,
                [None, org_id, True, 1, 1000]
            )
            if not org_settings or not org_settings[0]:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No valid settings found for organization: {org_id}"
                )

            actioners = []
            for org_setting in org_settings[0]:
                org_setting_id = org_setting.get("id")
                org_gate_settings = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_ORG_GATE_SETTING,
                    [None, org_setting_id, None, True, 1, 100000]
                )
                if not org_gate_settings or not org_gate_settings[0]:
                    # No gate settings configured for this org_setting; skip rather than fail.
                    continue

                for gate_setting in org_gate_settings[0]:
                    gate_setting_id = gate_setting.get("id")
                    if gate_setting_id is None:
                        continue
                    gate_actioners = await self.get_org_actioners_by_org_gate_setting_id(gate_setting_id)
                    # Skip gate settings that have no fixed actioners configured.
                    if gate_actioners and gate_actioners.actioner_hierarchy:
                        actioners.append(gate_actioners)

            return OrgActionersResponse(
                organization_id = org_id,
                actioners = actioners
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error fetching organizational actioners: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")
