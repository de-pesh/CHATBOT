from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm

from app.controllers.auth_controller import AuthController
from app.dependencies import get_db_service
from app.models.auth import Token, LoginRequest, ChangePasswordRequest
from app.dependencies import get_current_user
from app.models.auth import CurrentUser
from app.services.database_service import DatabaseService

router = APIRouter()

@router.post(
    "/authenticate",
    response_model=Token,
    summary="Authenticate user and get access token",
    description="Authenticates a user with email and password and returns an access token."
)
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db_service: DatabaseService = Depends(get_db_service)
) -> Token:
    """Endpoint to authenticate a user and return an access token."""
    login_request = LoginRequest(email=form_data.username, password=form_data.password)
    controller = AuthController(db_service)
    return await controller.login_for_access_token(login_request)

@router.post(
    "/change-password",
    summary="Change user password",
    description="Allows an authenticated user to change their password."
)
async def change_password(
    change_password_request: ChangePasswordRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_db_service)
):
    """Endpoint to change the password for the authenticated user."""
    controller = AuthController(db_service)
    return await controller.change_password(
        user_id=current_user.id,
        change_password_request=change_password_request,
        updated_by=current_user.email
    )

@router.post(
    "/re-authenticate",
    response_model=Token,
    summary="Refresh access token",
    description="Refreshes an existing access token, extending the user's session.",
)
async def refresh_token(
    current_user: CurrentUser = Depends(get_current_user),
    db_service: DatabaseService = Depends(get_db_service),
) -> Token:
    """Endpoint to refresh an access token for the authenticated user."""
    controller = AuthController(db_service)
    return controller.refresh_access_token(current_user)
