from typing import List

from fastapi import APIRouter, Depends, status, Query

from app.controllers.user_role_assignment_permission_controller import UserRoleAssignmentPermissionController
from app.dependencies import get_db_service, CurrentUserDep, has_permission
from app.models.auth import(
    UserRoleAssignmentPermissionCreate,
    UserRoleAssignmentPermissionResponse,
    UserRoleAssignmentPermissionUpdate,
    UserRoleAssignmentPermissionInDB,
    UserContextResponse
)
from app.services.database_service import DatabaseService
from app.security import Permission

router = APIRouter()

@router.post(
    "/",
    response_model=List[UserRoleAssignmentPermissionInDB],
    status_code=status.HTTP_201_CREATED,
    summary="Create new user-role-permission assignments",
    description="Creates one or more user-role-permission assignments for a user.",
    dependencies=[Depends(has_permission([Permission.USER_ROLE_ASSIGNMENT_PERMISSION_CREATE]))]
)
async def create_user_role_assignment_permission(
    urap_data: UserRoleAssignmentPermissionCreate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[UserRoleAssignmentPermissionInDB]:
    """Endpoint to create new user-role-permission assignments."""
    controller = UserRoleAssignmentPermissionController(db_service)
    return await controller.create_user_role_assignment_permission(urap_data, user_id=current_user.email)

@router.get(
    "/{urap_id}",
    response_model=List[UserRoleAssignmentPermissionInDB],
    status_code=status.HTTP_200_OK,
    summary="Retrieve a user-role-permission assignment by ID",
    description="Retrieves the details of a single user-role-permission assignment using its unique ID.",
    dependencies=[Depends(has_permission([Permission.USER_ROLE_ASSIGNMENT_PERMISSION_READ]))]
)
async def get_user_role_assignment_permission_by_id(
    urap_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[UserRoleAssignmentPermissionInDB]:
    """Endpoint to retrieve a user-role-permission assignment by its ID."""
    controller = UserRoleAssignmentPermissionController(db_service)
    return await controller.get_all_user_role_assignment_permissions(urap_id=urap_id, page_number=1, page_size=1)

@router.get(
    "/",
    response_model=UserRoleAssignmentPermissionResponse, # Changed to Wrapper Model
    status_code=status.HTTP_200_OK,
    summary="Retrieve all user-role-permission assignments",
    description="Retrieves a list of all user-role-permission assignments currently in the system.",
    dependencies=[Depends(has_permission([Permission.USER_ROLE_ASSIGNMENT_PERMISSION_READ]))]
)
async def get_all_user_role_assignment_permissions(
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
    page_number: int = Query(None, ge=1, description="Page number to retrieve"),
    page_size: int = Query(None, ge=1, le=100, description="Number of assignments per page")
) -> UserRoleAssignmentPermissionResponse:
    """Endpoint to retrieve all user-role-permission assignments."""
    controller = UserRoleAssignmentPermissionController(db_service)
    return await controller.get_all_user_role_assignment_permissions(page_number=page_number, page_size=page_size, count_required=True)

@router.get(
    "/user/{userid}",
    response_model=UserRoleAssignmentPermissionResponse, # Changed to Wrapper Model
    status_code=status.HTTP_200_OK,
    summary="Retrieve all user-role-permission assignments by user id",
    description="Retrieves a list of all user-role-permission assignments by user id currently in the system.",
    dependencies=[Depends(has_permission([Permission.USER_ROLE_ASSIGNMENT_PERMISSION_READ]))]
)
async def get_all_user_role_assignment_permissions_by_user(
    userid: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
    page_number: int = Query(None, ge=1, description="Page number to retrieve"),
    page_size: int = Query(None, ge=1, le=100, description="Number of assignments per page")
) -> UserRoleAssignmentPermissionResponse:
    """Endpoint to retrieve all user-role-permission assignments for a specific user."""
    controller = UserRoleAssignmentPermissionController(db_service)
    return await controller.get_all_user_role_assignment_permissions(
        user_id=userid, 
        page_number=page_number, 
        page_size=page_size, 
        count_required=True
    )

@router.get(
    "/user/{user_id}/details", 
    response_model=UserContextResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve full Role and Permission details for a User",
    description="Returns a hierarchical view of all Roles assigned to the user, and the Permissions contained within those roles.",
    dependencies=[Depends(has_permission([Permission.USER_ROLE_ASSIGNMENT_PERMISSION_READ]))]
)
async def get_user_roles_and_permissions_details(
    user_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> UserContextResponse:
    """Endpoint to retrieve roles and permissions details for a user."""
    controller = UserRoleAssignmentPermissionController(db_service)
    return await controller.get_user_roles_and_permissions_details(user_id=user_id)

@router.put(
    "/user/{user_id}",
    response_model=List[UserRoleAssignmentPermissionInDB],
    status_code=status.HTTP_200_OK,
    summary="Synchronize user-role-permission assignments for a user",
    description="Synchronizes the set of role-permission assignments for a user. The provided list of IDs will become the user's new set of active assignments. Any active assignments not on the list will be deactivated.",
    dependencies=[Depends(has_permission([Permission.USER_ROLE_ASSIGNMENT_PERMISSION_UPDATE]))]
)
async def update_user_role_assignment_permission(
    user_id: int,
    urap_data: UserRoleAssignmentPermissionUpdate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[UserRoleAssignmentPermissionInDB]:
    """Endpoint to synchronize user-role-permission assignments for a user."""
    controller = UserRoleAssignmentPermissionController(db_service)
    return await controller.update_user_role_assignment_permission(user_id, urap_data, updated_by_user=current_user.email)


@router.patch(
    "/{user_id}",
    response_model=List[UserRoleAssignmentPermissionInDB],
    status_code=status.HTTP_200_OK,
    summary="Synchronize user-role-permission assignments for a user (PATCH compatibility)",
    description="Compatibility endpoint for clients using PATCH on /user-role-assignments/{user_id}. Applies the same synchronization logic as PUT /user/{user_id}.",
    dependencies=[Depends(has_permission([Permission.USER_ROLE_ASSIGNMENT_PERMISSION_UPDATE]))]
)
async def patch_user_role_assignment_permission(
    user_id: int,
    urap_data: UserRoleAssignmentPermissionUpdate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[UserRoleAssignmentPermissionInDB]:
    """Compatibility PATCH endpoint to synchronize user-role-permission assignments for a user."""
    controller = UserRoleAssignmentPermissionController(db_service)
    return await controller.update_user_role_assignment_permission(
        user_id, urap_data, updated_by_user=current_user.email
    )

@router.delete(
    "/{urap_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a user-role-permission assignment",
    description="Deletes a user-role-permission assignment from the system using its unique ID.",
    dependencies=[Depends(has_permission([Permission.USER_ROLE_ASSIGNMENT_PERMISSION_DELETE]))]
)
async def delete_user_role_assignment_permission(
    urap_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> None:
    """Endpoint to delete a user-role-permission assignment."""
    controller = UserRoleAssignmentPermissionController(db_service)
    await controller.delete_user_role_assignment_permission(urap_id, user_id=current_user.email)
    return None
