from typing import Annotated, List, Optional
import aiohttp
from fastapi import Depends, Request, HTTPException, status
from datetime import datetime

from app.config import Settings, get_settings
from app.services.database_service import DatabaseService
from app.services.queue_service import MessageQueueService
from app.models.auth import CurrentUser
from app.security import decode_access_token
from app.services.storage_service import FileStorageService


# Optional token extractor: returns the bearer token if present, otherwise None.
def oauth2_scheme(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth:
        return None
    auth = auth.strip()
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1]
    return auth


def get_db_service(request: Request) -> DatabaseService:
    """Dependency that provides the database service instance from the application state."""
    return request.app.state.db_service


def get_file_storage_service(request: Request) -> FileStorageService:
    """Dependency that provides the File Storage Service instance from the application state."""
    return request.app.state.file_storage_service


def get_chatbot_queue(request: Request) -> MessageQueueService:
    """Dependency that provides the configured queue backend from the application state."""
    return request.app.state.redis_chatbot_queue


def get_http_client(request: Request) -> aiohttp.ClientSession:
    """Dependency that provides the shared aiohttp ClientSession for internal service-to-service calls."""
    return request.app.state.http_client


async def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db_service: DatabaseService = Depends(get_db_service),
    settings: Settings = Depends(get_settings)
) -> CurrentUser:
    # If authentication is disabled via settings, return a default dev user.
    if getattr(settings, "DISABLE_AUTH", False):
        dev_user = CurrentUser(
            id=0,
            first_name="Dev",
            last_name="User",
            email="dev@local.dev",
            organization_id=None,
            is_active=True,
            hashed_password="",
            created_on=datetime.utcnow(),
            created_by="system",
            permissions=["*"]
        )
        return dev_user

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_payload = decode_access_token(token, settings)
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    email: str = token_payload.get("sub")
    if email is None:
        raise credentials_exception
    
    # Fetch user from DB using email from token
    from app.stored_procedures import StoredProcedures # Import here to avoid circular dependency
    from app.models.auth import UserInDB

    user_results = await db_service.call_procedure(
        StoredProcedures.FILTER_USERS,
        [None, None, None, email, None, None, None, None]
    )
    
    if not user_results or not user_results[0]:
        raise credentials_exception
    
    user_in_db = UserInDB(**user_results[0][0])

    # Fetch user permissions
    permissions_results = await db_service.call_procedure(
        StoredProcedures.GET_USER_PERMISSIONS,
        [user_in_db.id]
    )
    permissions = [p["name"] for p in permissions_results[0]] if permissions_results and permissions_results[0] else []

    user = CurrentUser(**user_in_db.model_dump(), permissions=permissions)
    return user


def has_permission(required_permissions: List[str]):
    def permission_checker(
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
        settings: Settings = Depends(get_settings),
    ):
        if getattr(settings, "DISABLE_AUTH", False):
            return True
        for permission in required_permissions:
            if permission not in current_user.permissions:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Not enough permissions. Missing: {permission}"
                )
        return True
    return permission_checker

# Define a type hint for annotated dependencies
DbServiceDep = Annotated[DatabaseService, Depends(get_db_service)]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
