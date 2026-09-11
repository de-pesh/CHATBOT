from fastapi import APIRouter, Depends, status
from typing import List
from app.controllers.org_unit_value_controller import OrgUnitValueController
from app.dependencies import get_db_service, has_permission, CurrentUserDep
from app.models.org import OrgUnitValueCreatePayload, OrgUnitValueUpdatePayload, OrgUnitValuesResponse, OrgUnitValueInDB
from app.services.database_service import DatabaseService
from app.core.permissions import Permission


router = APIRouter()


@router.post(
    "/",
    response_model=OrgUnitValuesResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create new organization unit values",
    description="Creates new organization unit values with the provided details.",
    dependencies=[Depends(has_permission([Permission.ORG_UNIT_VALUE_CREATE]))],
)
async def create_org_unit_values(
    org_unit_value_data: OrgUnitValueCreatePayload,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> OrgUnitValuesResponse:
    """Endpoint to create new organization unit values."""
    controller = OrgUnitValueController(db_service)
    return await controller.create_org_unit_values(org_unit_value_data, current_user.email)


@router.get(
    "/organization/{org_id}",
    response_model=OrgUnitValuesResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve organization unit values by organization ID",
    description="Retrieves the details of organization unit values using its organization ID.",
    dependencies=[Depends(has_permission([Permission.ORG_UNIT_VALUE_READ]))],
)
async def get_org_unit_values_by_org_id(
    org_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> OrgUnitValuesResponse:
    """Endpoint to retrieve organization unit values by its organization ID."""
    controller = OrgUnitValueController(db_service)
    return await controller.get_org_unit_values_by_org_id(org_id)


@router.get(
    "/organization/{org_id}/unit/{org_unit_id}",
    response_model=List[OrgUnitValueInDB],
    status_code=status.HTTP_200_OK,
    summary="Retrieve organization unit values by organization ID for specific organization unit",
    description="Retrieves the details of organization unit values using its organization ID for specific organization unit.",
    dependencies=[Depends(has_permission([Permission.ORG_UNIT_VALUE_READ]))],
)
async def get_org_unit_values_by_org_id_by_org_unit(
    org_id: int,
    org_unit_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> OrgUnitValuesResponse:
    """Endpoint to retrieve organization unit values by its organization ID for specific organization unit."""
    controller = OrgUnitValueController(db_service)
    return await controller.get_org_unit_values_by_org_id_by_org_unit(org_id, org_unit_id)


@router.patch(
    "/{org_unit_value_id}",
    response_model=OrgUnitValuesResponse,
    status_code=status.HTTP_200_OK,
    summary="Update an existing organization unit value.",
    description="Updates the details of an existing organization unit value using its unique ID.",
    dependencies=[Depends(has_permission([Permission.ORG_UNIT_VALUE_UPDATE]))],
)
async def update_org_unit_value(
    org_unit_value_id: int,
    org_unit_value_data: OrgUnitValueUpdatePayload,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> OrgUnitValuesResponse:
    """Endpoint to update an existing organization unit value."""
    controller = OrgUnitValueController(db_service)
    return await controller.update_org_unit_value(org_unit_value_id, org_unit_value_data, current_user.email)


@router.delete(
    "/organization/{org_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete organizational unit values by organization",
    description="Deletes organizational unit values by organization.",
    dependencies=[Depends(has_permission([Permission.ORG_UNIT_VALUE_DELETE]))],
)
async def delete_org_unit_values_by_org_id(
    org_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> None:
    controller = OrgUnitValueController(db_service)
    await controller.delete_org_unit_values_by_org_id(org_id, current_user.email)
    return None
