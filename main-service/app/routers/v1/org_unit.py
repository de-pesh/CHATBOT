from typing import Optional
from fastapi import APIRouter, Depends, status

from app.controllers.org_unit_controller import OrgUnitController
from app.dependencies import get_db_service, has_permission, CurrentUserDep
from app.models.org import OrgUnitCreatePayload, OrgUnitUpdatePayload, OrgUnitsResponse
from app.services.database_service import DatabaseService
from app.core.permissions import Permission


router = APIRouter()


@router.post(
    "/",
    response_model=OrgUnitsResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create new organization units",
    description="Creates new organization units with the provided details.",
    dependencies=[Depends(has_permission([Permission.ORG_UNIT_CREATE]))],
)
async def create_org_units(
    org_unit_data: OrgUnitCreatePayload,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> OrgUnitsResponse:
    """Endpoint to create new organization units."""
    controller = OrgUnitController(db_service)
    return await controller.create_org_units(org_unit_data, current_user.email)


@router.get(
    "/organization/{org_id}",
    response_model=Optional[OrgUnitsResponse],
    status_code=status.HTTP_200_OK,
    summary="Retrieve organization units by organization ID",
    description="Retrieves the details of organization units using its organization ID.",
    dependencies=[Depends(has_permission([Permission.ORG_UNIT_READ]))],
)
async def get_org_units_by_org_id(
    org_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> Optional[OrgUnitsResponse]:
    """Endpoint to retrieve organization units by its organization ID."""
    controller = OrgUnitController(db_service)
    return await controller.get_org_units_by_org_id(org_id)


@router.patch(
    "/{org_unit_id}",
    response_model=OrgUnitsResponse,
    status_code=status.HTTP_200_OK,
    summary="Update an existing organization unit.",
    description="Updates the details of an existing organization unit using its unique ID.",
    dependencies=[Depends(has_permission([Permission.ORG_UNIT_UPDATE]))],
)
async def update_org_unit(
    org_unit_id: int,
    org_unit_data: OrgUnitUpdatePayload,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> OrgUnitsResponse:
    """Endpoint to update an existing organization unit."""
    controller = OrgUnitController(db_service)
    return await controller.update_org_unit(org_unit_id, org_unit_data, current_user.email)


@router.delete(
    "/organization/{org_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete organizational units by organization",
    description="Deletes organizational units by organization.",
    dependencies=[Depends(has_permission([Permission.ORG_UNIT_DELETE]))],
)
async def delete_org_units_by_org_id(
    org_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> None:
    controller = OrgUnitController(db_service)
    await controller.delete_org_units_by_org_id(org_id, current_user.email)
    return None
