from typing import List

from fastapi import APIRouter, Depends, status

from app.controllers.org_gate_settings_controller import OrgGateSettingsController
from app.dependencies import get_db_service, has_permission, CurrentUserDep
from app.models.org import (
    OrgGateSettingsCreate,
    OrgGateSettingsUpdate,
    OrgGateSettingsResponse,
    OrgAndGateSettingsUpsertRequest,
    OrgAndGateSettingsUpsertResponse,
)
from app.services.database_service import DatabaseService
from app.core.permissions import Permission

router = APIRouter()

@router.post(
    "/",
    response_model=OrgGateSettingsResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create new organization gate settings",
    description="Creates new gate settings for an organization.",
    dependencies=[Depends(has_permission([Permission.ORG_GATE_SETTINGS_CREATE]))]
)
async def create_org_gate_settings(
    org_gate_settings_data: OrgGateSettingsCreate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> OrgGateSettingsResponse:
    controller = OrgGateSettingsController(db_service)
    return await controller.create_org_gate_settings(org_gate_settings_data, current_user.email)


@router.post(
    "/organization/{organization_id}/upsert",
    response_model=OrgAndGateSettingsUpsertResponse,
    status_code=status.HTTP_200_OK,
    summary="Upsert organization and gate settings",
    description="Creates or updates organization settings and gate settings together in a single request.",
    dependencies=[Depends(has_permission([
        Permission.ORG_SETTINGS_CREATE,
        Permission.ORG_SETTINGS_UPDATE,
        Permission.ORG_GATE_SETTINGS_CREATE,
        Permission.ORG_GATE_SETTINGS_UPDATE,
    ]))]
)
async def upsert_org_and_gate_settings(
    organization_id: int,
    payload: OrgAndGateSettingsUpsertRequest,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
) -> OrgAndGateSettingsUpsertResponse:
    controller = OrgGateSettingsController(db_service)
    return await controller.upsert_org_and_gate_settings(organization_id, payload, current_user.email)


@router.get(
    "/{org_gate_settings_id}",
    response_model=OrgGateSettingsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get organization gate settings by ID",
    description="Retrieves organization gate settings by their unique ID.",
    dependencies=[Depends(has_permission([Permission.ORG_GATE_SETTINGS_READ]))]
)
async def get_org_gate_settings_by_id(
    org_gate_settings_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> OrgGateSettingsResponse:
    controller = OrgGateSettingsController(db_service)
    return await controller.get_org_gate_settings_by_id(org_gate_settings_id)


@router.get(
    "/organization/{organization_id}",
    response_model=List[OrgGateSettingsResponse],
    status_code=status.HTTP_200_OK,
    summary="Get organization gate settings by organization ID",
    description="Retrieves all gate settings for a given organization.",
    dependencies=[Depends(has_permission([Permission.ORG_GATE_SETTINGS_READ]))]
)
async def get_org_gate_settings_by_organization_id(
    organization_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[OrgGateSettingsResponse]:
    controller = OrgGateSettingsController(db_service)
    return await controller.get_org_gate_settings_by_organization_id(organization_id)


@router.patch(
    "/{org_gate_settings_id}",
    response_model=OrgGateSettingsResponse,
    status_code=status.HTTP_200_OK,
    summary="Update organization gate settings",
    description="Updates existing organization gate settings.",
    dependencies=[Depends(has_permission([Permission.ORG_GATE_SETTINGS_UPDATE]))]
)
async def update_org_gate_settings(
    org_gate_settings_id: int,
    org_gate_settings_data: OrgGateSettingsUpdate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> OrgGateSettingsResponse:
    controller = OrgGateSettingsController(db_service)
    return await controller.update_org_gate_settings(org_gate_settings_id, org_gate_settings_data, current_user.email)


@router.delete(
    "/{org_gate_settings_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete organization gate settings",
    description="Deletes organization gate settings by their unique ID.",
    dependencies=[Depends(has_permission([Permission.ORG_GATE_SETTINGS_DELETE]))]
)
async def delete_org_gate_settings(
    org_gate_settings_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> None:
    controller = OrgGateSettingsController(db_service)
    await controller.delete_org_gate_settings(org_gate_settings_id, current_user.email)
    return None
