import logging
from typing import List

from fastapi import HTTPException, status

from app.core.stored_procedures import StoredProcedures
from app.models.org import OrgSettingsCreate, OrgSettingsUpdate, OrgSettingsInDB
from app.services.database_service import DatabaseService

from app.controllers.organization_controller import OrganizationController

logger = logging.getLogger(__name__)

class OrgSettingsController:
    def __init__(self, db_service: DatabaseService):
        self.db_service = db_service

    async def create_org_settings(self, org_settings_data: OrgSettingsCreate, user_id: str) -> OrgSettingsInDB:
        # 1. Validate that the organization exists
        org_controller = OrganizationController(self.db_service)
        await org_controller.get_organization_by_id(org_settings_data.organization_id)

        # 2. Check if settings for this organization already exist
        existing_settings = await self.get_org_settings_by_organization_id(org_settings_data.organization_id)
        if existing_settings:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Settings for this organization already exist.")

        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ORG_SETTING,
                [
                    None,
                    org_settings_data.organization_id,
                    org_settings_data.retry_count,
                    org_settings_data.is_notification_required,
                    True,
                    user_id
                ]
            )
            if not results or not results[0] or 'id' not in results[0][0]:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create organization settings.")
            
            new_org_settings_id = results[0][0]['id']
            return await self.get_org_settings_by_id(new_org_settings_id)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error creating organization settings: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")


    async def get_org_settings_by_id(self, org_settings_id: int) -> OrgSettingsInDB:
        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ORG_SETTINGS,
                [org_settings_id, None, True, 1, 1]
            )
            if not results or not results[0]:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization settings not found.")
            
            return OrgSettingsInDB(**results[0][0])

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error retrieving organization settings {org_settings_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")


    async def get_org_settings_by_organization_id(self, organization_id: int) -> List[OrgSettingsInDB]:
        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_ORG_SETTINGS,
                [None, organization_id, True, 1, 1000]
            )
            if not results or not results[0]:
                return []
            
            return [OrgSettingsInDB(**settings) for settings in results[0]]

        except Exception as e:
            logger.exception(f"Error retrieving organization settings for organization {organization_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")


    async def update_org_settings(self, org_settings_id: int, org_settings_data: OrgSettingsUpdate, user_id: str) -> OrgSettingsInDB:
        existing_settings = await self.get_org_settings_by_id(org_settings_id)

        update_params = {
            "id": org_settings_id,
            "organization_id": None,
            "retry_count": org_settings_data.retry_count if org_settings_data.retry_count is not None else existing_settings.retry_count,
            "is_notification_required": org_settings_data.is_notification_required if org_settings_data.is_notification_required is not None else existing_settings.is_notification_required,
            "is_active": org_settings_data.is_active if org_settings_data.is_active is not None else existing_settings.is_active,
            "user_id": user_id
        }

        try:
            await self.db_service.call_procedure(
                StoredProcedures.UPSERT_ORG_SETTING,
                list(update_params.values())
            )

            return await self.get_org_settings_by_id(org_settings_id)

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error updating organization settings {org_settings_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")


    async def delete_org_settings(self, org_settings_id: int, user_id: str) -> dict:
        try:
            await self.get_org_settings_by_id(org_settings_id)
            await self.db_service.call_procedure(
                StoredProcedures.DELETE_ORG_SETTING,
                [org_settings_id, user_id]
            )
            return {"message": "Organization settings deleted successfully."}
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error deleting organization settings {org_settings_id}: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")
