from typing import List, Optional

from fastapi import APIRouter, Depends, status, Query

from app.controllers.user_controller import UserController
from app.dependencies import CurrentUserDep, get_db_service, has_permission
from app.models.auth import UserCreate, UserResponse, UserUpdate, UserSafeOutput
from app.services.database_service import DatabaseService
from app.core.permissions import Permission

router = APIRouter()

@router.post(
    "/",
    response_model=List[UserSafeOutput],
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user",
    description="Creates a new user with the provided details.",
    dependencies=[Depends(has_permission([Permission.USER_CREATE]))]
)
async def create_user(
    user_data: UserCreate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[UserSafeOutput]:
    """Endpoint to create a new user."""
    controller = UserController(db_service)
    return await controller.create_user(user_data, created_by=current_user.email)

@router.get(
    "/{user_id}",
    response_model=List[UserSafeOutput],
    status_code=status.HTTP_200_OK,
    summary="Retrieve a user by ID",
    description="Retrieves the details of a single user using its unique ID.",
    dependencies=[Depends(has_permission([Permission.USER_READ]))]
)
async def get_user_by_id(
    user_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[UserSafeOutput]:
    """Endpoint to retrieve a user by its ID."""
    controller = UserController(db_service)
    return await controller.get_all_users(user_id=user_id, page_number=1, page_size=1)

@router.get(
    "/",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve all users",
    description="Retrieves a list of all users currently in the system with pagination.",
    dependencies=[Depends(has_permission([Permission.USER_READ]))]
)
async def get_all_users(
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service),
    first_name: Optional[str] = Query(None, description="Filter by first name"),
    last_name: Optional[str] = Query(None, description="Filter by last name"),
    email: Optional[str] = Query(None, description="Filter by email"),
    organization_id: Optional[int] = Query(None, description="Filter by organization ID"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    page_number: int = Query(None, ge=1, description="Page number to retrieve"),
    page_size: int = Query(None, ge=1, le=100, description="Number of users per page"),
    role_name: Optional[str] = Query(None, description="Filter by role name")
) -> UserResponse:
    """Endpoint to retrieve all users with pagination."""
    controller = UserController(db_service)
    return await controller.get_all_users(
        first_name=first_name,
        last_name=last_name,
        email=email,
        organization_id=organization_id,
        is_active=is_active,
        page_number=page_number, 
        page_size=page_size, 
        count_required=True,
        role_name=role_name
    )
@router.patch(
    "/{user_id}",
    response_model=List[UserSafeOutput],
    status_code=status.HTTP_200_OK,
    summary="Update an existing user",
    description="Updates the details of an existing user using its unique ID. Partial updates are supported.",
    dependencies=[Depends(has_permission([Permission.USER_UPDATE]))]
)
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> List[UserSafeOutput]:
    """Endpoint to update an existing user."""
    controller = UserController(db_service)
    return await controller.update_user(user_id, user_data, current_user.email)

@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a user",
    description="Deletes a user from the system using its unique ID.",
    dependencies=[Depends(has_permission([Permission.USER_DELETE]))]
)
async def delete_user(
    user_id: int,
    current_user: CurrentUserDep,
    db_service: DatabaseService = Depends(get_db_service)
) -> None:
    """Endpoint to delete a user."""
    controller = UserController(db_service)
    await controller.delete_user(user_id, deleted_by=current_user.email)
    return None
