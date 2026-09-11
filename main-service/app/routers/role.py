from typing import List

from fastapi import APIRouter, Depends, status, Query
from fastapi.responses import JSONResponse

from app.controllers.role_controller import RoleController
from app.dependencies import CurrentUserDep, get_db_service, has_permission
from app.models.auth import RoleCreate, RoleResponse, RoleUpdate, RoleInDB
from app.services.database_service import DatabaseService
from app.security import Permission

router = APIRouter()

@router.post(
    "/",
    response_model=List[RoleInDB],
    status_code=status.HTTP_201_CREATED,
    summary="Create a new role",
    description="Creates a new role with the provided details.",
    dependencies=[Depends(has_permission([Permission.ROLE_CREATE]))]
)
async def create_role(
    role_data: RoleCreate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[RoleInDB]:
    """Endpoint to create a new role."""
    controller = RoleController(db_service)
    return await controller.create_role(role_data, user_id=current_user.email)

@router.get(
    "/{role_id}",
    response_model=List[RoleInDB],
    status_code=status.HTTP_200_OK,
    summary="Retrieve a role by ID",
    description="Retrieves the details of a single role using its unique ID.",
    dependencies=[Depends(has_permission([Permission.ROLE_READ]))]
)
async def get_role_by_id(
    role_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[RoleInDB]:
    """Endpoint to retrieve a role by its ID."""
    controller = RoleController(db_service)
    return await controller.get_all_roles(role_id=role_id, page_number=1, page_size=1)


@router.get(
    "/",
    response_model=RoleResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve all roles",
    description="Retrieves a list of all roles currently in the system.",
    dependencies=[Depends(has_permission([Permission.ROLE_READ]))]
)
async def get_all_roles(
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
    page_number: int = Query(None, ge=1, description="Page number to retrieve"),
    page_size: int = Query(None, ge=1, le=100, description="Number of roles per page")
) -> RoleResponse:
    """Endpoint to retrieve all roles."""
    controller = RoleController(db_service)
    # Request count=True
    return await controller.get_all_roles(page_number=page_number, page_size=page_size, count_required=True)

@router.patch(
    "/{role_id}",
    response_model=List[RoleInDB],
    status_code=status.HTTP_200_OK,
    summary="Update an existing role",
    description="Updates the details of an existing role using its unique ID. Partial updates are supported.",
    dependencies=[Depends(has_permission([Permission.ROLE_UPDATE]))]
)
async def update_role(
    role_id: int,
    role_data: RoleUpdate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[RoleInDB]:
    """Endpoint to update an existing role."""
    controller = RoleController(db_service)
    return await controller.update_role(role_id, role_data, user_id=current_user.email)

@router.delete(
    "/{role_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a role",
    description="Deletes a role from the system using its unique ID.",
    dependencies=[Depends(has_permission([Permission.ROLE_DELETE]))]
)
async def delete_role(
    role_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> JSONResponse:
    """Endpoint to delete a role."""
    controller = RoleController(db_service)
    await controller.delete_role(role_id, user_id=current_user.email)
    return JSONResponse(content={"message": "Role deleted successfully"}, status_code=status.HTTP_200_OK)
