from typing import List

from fastapi import APIRouter, Depends, Query, status

from app.controllers.organization_controller import OrganizationController
from app.dependencies import CurrentUserDep, get_db_service, has_permission
from app.models.org import OrganizationCreate, OrganizationUpdate, OrganizationInDB, OrganizationResponse
from app.services.database_service import DatabaseService
from app.security import Permission


router = APIRouter()


@router.post(
    "/",
    response_model=List[OrganizationInDB],
    status_code=status.HTTP_201_CREATED,
    summary="Create a new organization",
    description="Creates a new organization with the provided details.",
    dependencies=[Depends(has_permission([Permission.ORGANIZATION_CREATE]))],
)
async def create_organization(
    org_data: OrganizationCreate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[OrganizationInDB]:
    """Endpoint to create a new organization."""
    controller = OrganizationController(db_service)
    return await controller.create_organization(org_data, current_user.email)


@router.get(
    "/{org_id}",
    response_model=List[OrganizationInDB],
    status_code=status.HTTP_200_OK,
    summary="Retrieve an organization by ID",
    description="Retrieves the details of a single organization using its unique ID.",
    dependencies=[Depends(has_permission([Permission.ORGANIZATION_READ]))]
)
async def get_organization_by_id(
    org_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[OrganizationInDB]:
    """Endpoint to retrieve an organization by its ID."""
    controller = OrganizationController(db_service)
    return await controller.get_all_organizations(org_id = org_id, page_number=1, page_size=1)


@router.get(
    "/",
    response_model=OrganizationResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve all organizations",
    description="Retrieves a list of all organizations currently in the system.",
    dependencies=[Depends(has_permission([Permission.ORGANIZATION_READ]))]
)
async def get_all_organizations(
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
    page_number: int = Query(None, ge=1, description="Page number to retrieve"),
    page_size: int = Query(None, ge=1, le=100, description="Number of organizations per page")
) -> OrganizationResponse:
    """Endpoint to retrieve all organizations."""
    controller = OrganizationController(db_service)
    return await controller.get_all_organizations(page_number=page_number, page_size=page_size, count_required=True)


@router.patch(
    "/{org_id}",
    response_model=List[OrganizationInDB],
    status_code=status.HTTP_200_OK,
    summary="Update an existing organization",
    description="Updates the details of an existing organization using its unique ID. Partial updates are supported.",
    dependencies=[Depends(has_permission([Permission.ORGANIZATION_UPDATE]))],
)
async def update_organization(
    org_id: int,
    org_data: OrganizationUpdate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[OrganizationInDB]:
    """Endpoint to update an existing organization."""
    controller = OrganizationController(db_service)
    return await controller.update_organization(org_id, org_data, current_user.email)


@router.delete(
    "/{org_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an organization",
    description="Deletes an organization from the system by marking it as inactive.",
    dependencies=[Depends(has_permission([Permission.ORGANIZATION_DELETE]))],
)
async def delete_organization(
    org_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> None:
    """Endpoint to delete an organization."""
    controller = OrganizationController(db_service)
    await controller.delete_organization(org_id, current_user.email)
    return None
