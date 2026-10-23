import logging

from app.config import get_settings
from app.models.auth import (
    ChangePasswordRequest,
    CurrentUser,
    LoginRequest,
    Token,
    UserCreate,
    UserInDB,
    UserUpdate,
)
from app.security import create_access_token, get_password_hash, verify_password
from app.services.db.database_service import DatabaseService
from app.services.db.stored_procedures import StoredProcedures
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

class AuthController:
    """
    Controller for handling authentication-related operations.

    This class encapsulates the business logic for user login and token generation.
    """

    def __init__(self, db_service: DatabaseService):
        self.db_service = db_service

    async def authenticate_user(self, email: str, password: str) -> CurrentUser:
        """
        Authenticates a user by email and password.

        Args:
            email (str): The user's email.
            password (str): The user's plain password.

        Returns:
            UserInDB: The authenticated user's data.

        Raises:
            HTTPException: If authentication fails.
        """
        try:
            results = await self.db_service.call_procedure(
                StoredProcedures.FILTER_USERS,
                [None, None, None, email, None, None, None, None]
            )
            if not results or not results[0]:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")

            user_in_db = UserInDB(**results[0][0])
            current_user = CurrentUser(**user_in_db.model_dump())

            if not verify_password(password, current_user.hashed_password):
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")
            
            return current_user
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error during user authentication: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred during authentication: {e}") from e

    async def login_for_access_token(self, login_request: LoginRequest) -> Token:
        """
        Handles user login and generates an access token.

        Args:
            login_request (LoginRequest): The login request containing email and password.

        Returns:
            Token: An access token and token type.

        Raises:
            HTTPException: If authentication fails.
        """
        user = await self.authenticate_user(login_request.email, login_request.password)
        settings = get_settings()
        claims: dict = {"sub": user.email}
        if getattr(settings, "TENANT_CONTEXT_ENABLED", False):
            # Embed the tenant/identity context so get_current_user can read it
            # back from the token on every subsequent request (dependencies.py).
            claims["entity_id"] = user.entity_id
            claims["tenant_id"] = user.tenant_id
            claims["user_type"] = user.user_type
        access_token = create_access_token(data=claims)
        return Token(access_token=access_token, token_type="bearer", role=user.role)

    def refresh_access_token(self, current_user: CurrentUser) -> Token:
        """
        Refreshes an access token for the given user.

        Args:
            current_user (CurrentUser): The current authenticated user.

        Returns:
            Token: A new access token with a renewed expiration time.
        """
        settings = get_settings()
        claims: dict = {"sub": current_user.email}
        if getattr(settings, "TENANT_CONTEXT_ENABLED", False):
            claims["entity_id"] = current_user.entity_id
            claims["tenant_id"] = current_user.tenant_id
            claims["user_type"] = current_user.user_type
        access_token = create_access_token(data=claims)
        return Token(access_token=access_token, token_type="bearer", role=current_user.role)

    async def create_user(self, request: UserCreate, created_by: str) -> CurrentUser:
        """
        Creates a new account in dbo.users. Admin-only (see require_admin on the route).

        Args:
            request (UserCreate): The new account's name, email, password and role.
            created_by (str): The admin's email, recorded as dbo.users.created_by.

        Raises:
            HTTPException: If an account with that email already exists.
        """
        existing = await self.db_service.call_procedure(
            StoredProcedures.FILTER_USERS,
            [None, None, None, request.email, None, None, None, None]
        )
        if existing and existing[0]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A user with email {request.email!r} already exists."
            )

        result = await self.db_service.call_procedure(
            StoredProcedures.UPSERT_USER,
            [
                None,  # id: new account
                request.first_name,
                request.last_name,
                request.email,
                get_password_hash(request.password),
                request.role,
                request.is_active,
                created_by,
            ]
        )
        new_id = result[0][0]["id"]

        # upsert_user only returns the new id (see scripts/mssql/05_users.sql) - fetch the row it
        # just wrote so the response has the rest of the account, the same as every other read.
        created = await self.db_service.call_procedure(
            StoredProcedures.FILTER_USERS,
            [new_id, None, None, None, None, None, None, None]
        )
        logger.info(f"User {created_by} created new account {request.email!r} (role={request.role!r}).")
        return CurrentUser(**created[0][0])

    async def change_password(self, user_id: int, change_password_request: ChangePasswordRequest, updated_by: str):
        """
        Changes the password for a given user.

        Args:
            user_id (int): The ID of the user whose password is to be changed.
            change_password_request (ChangePasswordRequest): The request containing old and new passwords.
            updated_by (str): The identifier of the user performing the update.

        Raises:
            HTTPException: If passwords do not match, old password is incorrect, or update fails.
        """
        if change_password_request.new_password != change_password_request.confirm_new_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New password and confirmation do not match."
            )

        # Fetch user to verify old password
        user_results = await self.db_service.call_procedure(
            StoredProcedures.FILTER_USERS,
            [
                user_id,  # id
                None,     # first_name
                None,     # last_name
                None,     # email
                None,     # role
                None,     # is_active
                1,
                1
            ]
        )
        if not user_results or not user_results[0]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

        user_in_db = UserInDB(**user_results[0][0])

        if not verify_password(change_password_request.old_password, user_in_db.hashed_password):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect old password.")

        # Hash the new password
        hashed_new_password = get_password_hash(change_password_request.new_password)

        # Update the user's password in the database. role is left out (None): a
        # password change never changes what the account is allowed to do.
        user_update_data = UserUpdate(
            first_name=user_in_db.first_name,
            last_name=user_in_db.last_name,
            email=user_in_db.email,
            is_active=user_in_db.is_active
        )

        # Call UPSERT_USER with the new hashed password
        await self.db_service.call_procedure(
            StoredProcedures.UPSERT_USER,
            [
                user_id,
                user_update_data.first_name,
                user_update_data.last_name,
                user_update_data.email,
                hashed_new_password,  # Use the new hashed password
                None,  # role unchanged
                user_update_data.is_active,
                updated_by
            ]
        )
        logger.info(f"User {user_id} successfully changed password.")
        return {"message": "Password changed successfully."}
