import logging
from fastapi import HTTPException, status
from app.stored_procedures import StoredProcedures
from app.models.auth import LoginRequest, Token, ChangePasswordRequest
from app.models.auth import CurrentUser, UserInDB, UserUpdate
from app.services.database_service import DatabaseService
from app.security import verify_password, create_access_token, get_password_hash

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

            # Fetch user permissions
            # Fetch user permissions
            permissions_result = await self.db_service.call_procedure(
                StoredProcedures.GET_USER_PERMISSIONS,
                [user_in_db.id]
            )
            permissions = [p["name"] for p in permissions_result[0]] if permissions_result and permissions_result[0] else []

            current_user = CurrentUser(**user_in_db.model_dump(), permissions=permissions)

            if not verify_password(password, current_user.hashed_password):
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")
            
            return current_user
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error during user authentication: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred during authentication: {e}")

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
        access_token = create_access_token(data={"sub": user.email})
        return Token(access_token=access_token, token_type="bearer", permissions=user.permissions)

    def refresh_access_token(self, current_user: CurrentUser) -> Token:
        """
        Refreshes an access token for the given user.

        Args:
            current_user (CurrentUser): The current authenticated user.

        Returns:
            Token: A new access token with a renewed expiration time.
        """
        access_token = create_access_token(data={"sub": current_user.email})
        return Token(access_token=access_token, token_type="bearer", permissions=current_user.permissions)

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
                user_id,            # id
                None,         # first_name
                None,          # last_name
                None,              # email
                None,    # organization_id
                None,          # is_active
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

        # Update the user's password in the database
        user_update_data = UserUpdate(
            first_name=user_in_db.first_name,
            last_name=user_in_db.last_name,
            email=user_in_db.email,
            organization_id=user_in_db.organization_id,
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
                user_update_data.organization_id,
                user_update_data.is_active,
                updated_by
            ]
        )
        logger.info(f"User {user_id} successfully changed password.")
        return {"message": "Password changed successfully."}
