from typing import List

from fastapi import APIRouter, Depends, status, Query

from app.controllers.permission_controller import PermissionController
from app.dependencies import get_db_service, CurrentUserDep, has_permission
from app.models.auth import PermissionCreate, PermissionResponse, PermissionUpdate, PermissionInDB
from app.services.database_service import DatabaseService
from app.security import Permission

router = APIRouter()

@router.post(
    "/",
    response_model=List[PermissionInDB],
    status_code=status.HTTP_201_CREATED,
    summary="Create a new permission",
    description="Creates a new permission with the provided details.",
    dependencies=[Depends(has_permission([Permission.PERMISSION_CREATE]))]
)
async def create_permission(
    permission_data: PermissionCreate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[PermissionInDB]:
    """Endpoint to create a new permission."""
    controller = PermissionController(db_service)
    return await controller.create_permission(permission_data, user_id=current_user.email)

@router.get(
    "/{permission_id}",
    response_model=List[PermissionInDB],
    status_code=status.HTTP_200_OK,
    summary="Retrieve a permission by ID",
    description="Retrieves the details of a single permission using its unique ID.",
    dependencies=[Depends(has_permission([Permission.PERMISSION_READ]))]
)
async def get_permission_by_id(
    permission_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[PermissionInDB]:
    """Endpoint to retrieve a permission by its ID."""
    controller = PermissionController(db_service)
    # Reuse the common function, strictly filtering by ID with page size 1
    return await controller.get_all_permissions(permission_id=permission_id, page_number=1, page_size=1)

@router.get(
    "/",
    response_model=PermissionResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve all permissions",
    description="Retrieves a list of all permissions currently in the system.",
    dependencies=[Depends(has_permission([Permission.PERMISSION_READ]))]
)
async def get_all_permissions(
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
    page_number: int = Query(None, ge=1, description="Page number to retrieve"), # Default to None to allow 'get all' if logic permits
    page_size: int = Query(None, ge=1, le=100, description="Number of permissions per page")
) -> PermissionResponse:
    """Endpoint to retrieve all permissions."""
    controller = PermissionController(db_service)
    return await controller.get_all_permissions(page_number=page_number, page_size=page_size, count_required=True)

@router.patch(
    "/{permission_id}",
    response_model=List[PermissionInDB],
    status_code=status.HTTP_200_OK,
    summary="Update an existing permission",
    description="Updates the details of an existing permission using its unique ID. Partial updates are supported.",
    dependencies=[Depends(has_permission([Permission.PERMISSION_UPDATE]))]
)
async def update_permission(
    permission_id: int,
    permission_data: PermissionUpdate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[PermissionInDB]:
    """Endpoint to update an existing permission."""
    controller = PermissionController(db_service)
    return await controller.update_permission(permission_id, permission_data, user_id=current_user.email)

@router.delete(
    "/{permission_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a permission",
    description="Deletes a permission from the system using its unique ID.",
    dependencies=[Depends(has_permission([Permission.PERMISSION_DELETE]))]
)
async def delete_permission(
    permission_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> None:
    """Endpoint to delete a permission."""
    controller = PermissionController(db_service)
    await controller.delete_permission(permission_id, user_id=current_user.email)
    return None
