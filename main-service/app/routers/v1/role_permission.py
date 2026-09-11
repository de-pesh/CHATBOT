from typing import List

from fastapi import APIRouter, Depends, status, Query

from app.controllers.role_permission_controller import RolePermissionController
from app.dependencies import get_db_service, CurrentUserDep, has_permission
from app.models.auth import RolePermissionResponse, RolePermissionUpdate, RolePermissionMultiCreate, RolePermissionInDB, RolePermissionSyncUpdate
from app.models.auth import PermissionResponse
from app.services.database_service import DatabaseService
from app.core.permissions import Permission

router = APIRouter()

@router.post(
    "/",
    response_model=List[RolePermissionInDB],
    status_code=status.HTTP_201_CREATED,
    summary="Create new role-permission assignments for a role",
    description="Creates new role-permission assignments for a single role with the provided list of permissions.",
    dependencies=[Depends(has_permission([Permission.ROLE_PERMISSION_CREATE]))]
)
async def create_role_permission(
    rp_data: RolePermissionMultiCreate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[RolePermissionInDB]:
    """Endpoint to create a new role-permission assignment."""
    controller = RolePermissionController(db_service)
    return await controller.create_role_permissions(rp_data, user_id=current_user.email)

@router.get(
    "/{rp_id}",
    response_model=List[RolePermissionInDB], # Changed to List to match Organization pattern
    status_code=status.HTTP_200_OK,
    summary="Retrieve a role-permission assignment by ID",
    description="Retrieves the details of a single role-permission assignment using its unique ID.",
    dependencies=[Depends(has_permission([Permission.ROLE_PERMISSION_READ]))]
)
async def get_role_permission_by_id(
    rp_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[RolePermissionInDB]:
    """Endpoint to retrieve a role-permission assignment by its ID."""
    controller = RolePermissionController(db_service)
    # Get specific ID, page 1, size 1, no count needed
    return await controller.get_all_role_permissions(rp_id=rp_id, page_number=1, page_size=1)

@router.get(
    "/",
    response_model=RolePermissionResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve all role-permission assignments",
    description="Retrieves a list of all role-permission assignments currently in the system.",
    dependencies=[Depends(has_permission([Permission.ROLE_PERMISSION_READ]))]
)
async def get_all_role_permissions(
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
    page_number: int = Query(None, ge=1, description="Page number to retrieve"),
    page_size: int = Query(None, ge=1, le=100, description="Number of assignments per page")
) -> RolePermissionResponse:
    """Endpoint to retrieve all role-permission assignments."""
    controller = RolePermissionController(db_service)
    # Request count=True
    return await controller.get_all_role_permissions(page_number=page_number, page_size=page_size, count_required=True)


@router.get(
    "/permission/{permission_id}",
    response_model=RolePermissionResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve all role-permission assignments by permission id",
    description="Retrieves a list of all role-permission assignments by permission id currently in the system.",
    dependencies=[Depends(has_permission([Permission.ROLE_PERMISSION_READ]))]
)
async def get_all_role_permissions_by_permission(
    permission_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
    page_number: int = Query(None, ge=1, description="Page number to retrieve"),
    page_size: int = Query(None, ge=1, le=100, description="Number of assignments per page")
) -> RolePermissionResponse:
    """Endpoint to retrieve all role-permission assignments for a specific permission."""
    controller = RolePermissionController(db_service)
    return await controller.get_all_role_permissions(
        permission_id=permission_id, 
        page_number=page_number, 
        page_size=page_size, 
        count_required=True
    )

@router.get(
    "/role/{role_id}",
    response_model=PermissionResponse, # Returns Permissions, not RolePermissions
    status_code=status.HTTP_200_OK,
    summary="Retrieve permissions by Role ID",
    description="Retrieves a list of all permissions assigned to a specific role.",
    dependencies=[Depends(has_permission([Permission.ROLE_PERMISSION_READ]))]
)
async def get_permissions_by_role(
    role_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
    page_number: int = Query(None, ge=1, description="Page number to retrieve"),
    page_size: int = Query(None, ge=1, le=100, description="Number of permissions per page")
) -> PermissionResponse:
    """Endpoint to retrieve actual permission details for a specific role."""
    controller = RolePermissionController(db_service)
    return await controller.get_permissions_by_role_id(
        role_id=role_id, 
        page_number=page_number, 
        page_size=page_size
    )

@router.patch(
    "/role/{role_id}",
    response_model=List[RolePermissionInDB],
    status_code=status.HTTP_200_OK,
    summary="Replace all assigned permissions for a role",
    description="Synchronizes role-permission assignments for a role. The provided permission ID list becomes the role's full active permission set; missing active permissions are deactivated.",
    dependencies=[Depends(has_permission([Permission.ROLE_PERMISSION_UPDATE]))]
)
async def replace_permissions_for_role(
    role_id: int,
    rp_data: RolePermissionSyncUpdate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[RolePermissionInDB]:
    """Endpoint to synchronize role-permission assignments for a role."""
    controller = RolePermissionController(db_service)
    return await controller.replace_permissions_for_role(role_id, rp_data, user_id=current_user.email)

@router.patch(
    "/{rp_id}",
    response_model=List[RolePermissionInDB],
    status_code=status.HTTP_200_OK,
    summary="Update an existing role-permission assignment",
    description="Updates the details of an existing role-permission assignment using its unique ID. Partial updates are supported.",
    dependencies=[Depends(has_permission([Permission.ROLE_PERMISSION_UPDATE]))]
)
async def update_role_permission(
    rp_id: int,
    rp_data: RolePermissionUpdate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[RolePermissionInDB]:
    """Endpoint to update an existing role-permission assignment."""
    controller = RolePermissionController(db_service)
    return await controller.update_role_permission(rp_id, rp_data, user_id=current_user.email)

@router.delete(
    "/{rp_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a role-permission assignment",
    description="Deletes a role-permission assignment from the system using its unique ID.",
    dependencies=[Depends(has_permission([Permission.ROLE_PERMISSION_DELETE]))]
)
async def delete_role_permission(
    rp_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> None:
    """Endpoint to delete a role-permission assignment."""
    controller = RolePermissionController(db_service)
    await controller.delete_role_permission(rp_id, user_id=current_user.email)
    return None
