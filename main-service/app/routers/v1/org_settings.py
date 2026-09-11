from typing import List

from fastapi import APIRouter, Depends, status

from app.controllers.org_settings_controller import OrgSettingsController
from app.dependencies import get_db_service, has_permission, CurrentUserDep
from app.models.org import OrgSettingsCreate, OrgSettingsUpdate, OrgSettingsResponse
from app.services.database_service import DatabaseService
from app.core.permissions import Permission

router = APIRouter()

@router.post(
    "/",
    response_model=OrgSettingsResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create new organization settings",
    description="Creates new settings for an organization.",
    dependencies=[Depends(has_permission([Permission.ORG_SETTINGS_CREATE]))]
)
async def create_org_settings(
    org_settings_data: OrgSettingsCreate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> OrgSettingsResponse:
    controller = OrgSettingsController(db_service)
    return await controller.create_org_settings(org_settings_data, current_user.email)


@router.get(
    "/{org_settings_id}",
    response_model=OrgSettingsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get organization settings by ID",
    description="Retrieves organization settings by their unique ID.",
    dependencies=[Depends(has_permission([Permission.ORG_SETTINGS_READ]))]
)
async def get_org_settings_by_id(
    org_settings_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> OrgSettingsResponse:
    controller = OrgSettingsController(db_service)
    return await controller.get_org_settings_by_id(org_settings_id)


@router.get(
    "/organization/{organization_id}",
    response_model=List[OrgSettingsResponse],
    status_code=status.HTTP_200_OK,
    summary="Get organization settings by organization ID",
    description="Retrieves all settings for a given organization.",
    dependencies=[Depends(has_permission([Permission.ORG_SETTINGS_READ]))]
)
async def get_org_settings_by_organization_id(
    organization_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[OrgSettingsResponse]:
    controller = OrgSettingsController(db_service)
    return await controller.get_org_settings_by_organization_id(organization_id)


@router.patch(
    "/{org_settings_id}",
    response_model=OrgSettingsResponse,
    status_code=status.HTTP_200_OK,
    summary="Update organization settings",
    description="Updates existing organization settings.",
    dependencies=[Depends(has_permission([Permission.ORG_SETTINGS_UPDATE]))]
)
async def update_org_settings(
    org_settings_id: int,
    org_settings_data: OrgSettingsUpdate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> OrgSettingsResponse:
    controller = OrgSettingsController(db_service)
    return await controller.update_org_settings(org_settings_id, org_settings_data, current_user.email)


@router.delete(
    "/{org_settings_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete organization settings",
    description="Deletes organization settings by their unique ID.",
    dependencies=[Depends(has_permission([Permission.ORG_SETTINGS_DELETE]))]
)
async def delete_org_settings(
    org_settings_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> None:
    controller = OrgSettingsController(db_service)
    await controller.delete_org_settings(org_settings_id, current_user.email)
    return None
