"""Authentication, password hashing and the permission vocabulary.

Merged from `core/auth_bearer.py`, `core/security.py` and `core/permissions.py`,
which were three small modules covering one concern: who the caller is and what
they are allowed to do.
"""
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

import bcrypt
from fastapi import HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from app.config import Settings, get_settings


settings: Settings = get_settings()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/authenticate")


# --- Passwords and token issuing -----------------------------------------------

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verifies a plain password against a hashed password.
    """
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))


def get_password_hash(password: str) -> str:
    """
    Hashes a password using bcrypt.
    """
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """
    Creates a JWT access token.
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY.get_secret_value(), algorithm=settings.ALGORITHM)
    return encoded_jwt


# --- Token validation ----------------------------------------------------------

def decode_access_token(
    token: str,
    settings: Settings
) -> dict:
    """
    Decodes and validates a JWT access token.

    Args:
        token (str): The JWT token string.
        settings (Settings): Application settings containing SECRET_KEY and ALGORITHM.

    Returns:
        dict: The decoded payload of the JWT.

    Raises:
        HTTPException: If the token is invalid, expired, or decoding fails.
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY.get_secret_value(),
            algorithms=[settings.ALGORITHM]
        )
        # Check for token expiration
        expiration_timestamp = payload.get("exp")
        if expiration_timestamp is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials: Token has no expiration.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        expiration_datetime = datetime.fromtimestamp(expiration_timestamp, tz=timezone.utc)
        if expiration_datetime < datetime.now(timezone.utc):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials: Token has expired.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials: Invalid token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
# --- Permissions ---------------------------------------------------------------

class Permission(str, Enum):
    # Organization Permissions
    ORGANIZATION_CREATE = "organization:create"
    ORGANIZATION_READ = "organization:read"
    ORGANIZATION_UPDATE = "organization:update"
    ORGANIZATION_DELETE = "organization:delete"

    # Asset Permissions
    ASSET_CREATE = "asset:create"
    ASSET_READ = "asset:read"
    ASSET_UPDATE = "asset:update"
    ASSET_DELETE = "asset:delete"

    # Asset Type Permissions
    ASSET_TYPE_CREATE = "asset_type:create"
    ASSET_TYPE_READ = "asset_type:read"
    ASSET_TYPE_UPDATE = "asset_type:update"
    ASSET_TYPE_DELETE = "asset_type:delete"

    # User Permissions
    USER_CREATE = "user:create"
    USER_READ = "user:read"
    USER_UPDATE = "user:update"
    USER_DELETE = "user:delete"

    # Role Permissions
    ROLE_CREATE = "role:create"
    ROLE_READ = "role:read"
    ROLE_UPDATE = "role:update"
    ROLE_DELETE = "role:delete"

    # Framework Permissions
    FRAMEWORK_CREATE = "framework:create"
    FRAMEWORK_READ = "framework:read"
    FRAMEWORK_UPDATE = "framework:update"
    FRAMEWORK_DELETE = "framework:delete"

    # Framework Configuration Permissions
    FRAMEWORK_CONFIGURATION_CREATE = "framework_configuration:create"
    FRAMEWORK_CONFIGURATION_READ = "framework_configuration:read"
    FRAMEWORK_CONFIGURATION_UPDATE = "framework_configuration:update"
    FRAMEWORK_CONFIGURATION_DELETE = "framework_configuration:delete"

    # Assessment Permissions
    ASSESSMENT_CREATE = "assessment:create"
    ASSESSMENT_READ = "assessment:read"
    ASSESSMENT_UPDATE = "assessment:update"
    ASSESSMENT_DELETE = "assessment:delete"

    # File Permissions
    FILE_CREATE = "file:create"
    FILE_READ = "file:read"
    FILE_DELETE = "file:delete"

    # RBAC Management Permissions
    PERMISSION_CREATE = "permission:create"
    PERMISSION_READ = "permission:read"
    PERMISSION_UPDATE = "permission:update"
    PERMISSION_DELETE = "permission:delete"

    ROLE_PERMISSION_CREATE = "role_permission:create"
    ROLE_PERMISSION_READ = "role_permission:read"
    ROLE_PERMISSION_UPDATE = "role_permission:update"
    ROLE_PERMISSION_DELETE = "role_permission:delete"

    USER_ROLE_ASSIGNMENT_PERMISSION_CREATE = "user_role_assignment_permission:create"
    USER_ROLE_ASSIGNMENT_PERMISSION_READ = "user_role_assignment_permission:read"
    USER_ROLE_ASSIGNMENT_PERMISSION_UPDATE = "user_role_assignment_permission:update"
    USER_ROLE_ASSIGNMENT_PERMISSION_DELETE = "user_role_assignment_permission:delete"

    # Organizational Unit Permissions
    ORG_UNIT_CREATE = "org_unit:create"
    ORG_UNIT_READ = "org_unit:read"
    ORG_UNIT_UPDATE = "org_unit:update"
    ORG_UNIT_DELETE = "org_unit:delete"

    # Organizational Unit Value Permissions
    ORG_UNIT_VALUE_CREATE = "org_unit_value:create"
    ORG_UNIT_VALUE_READ = "org_unit_value:read"
    ORG_UNIT_VALUE_UPDATE = "org_unit_value:update"
    ORG_UNIT_VALUE_DELETE = "org_unit_value:delete"

    # Org Settings Permissions
    ORG_SETTINGS_CREATE = "org_settings:create"
    ORG_SETTINGS_READ = "org_settings:read"
    ORG_SETTINGS_UPDATE = "org_settings:update"
    ORG_SETTINGS_DELETE = "org_settings:delete"

    # Org Gate Settings Permissions
    ORG_GATE_SETTINGS_CREATE = "org_gate_settings:create"
    ORG_GATE_SETTINGS_READ = "org_gate_settings:read"
    ORG_GATE_SETTINGS_UPDATE = "org_gate_settings:update"
    ORG_GATE_SETTINGS_DELETE = "org_gate_settings:delete"

    # Org Fixed Actioners Permissions
    ORG_FIXED_ACTIONERS_CREATE = "org_fixed_actioners:create"
    ORG_FIXED_ACTIONERS_READ = "org_fixed_actioners:read"
    ORG_FIXED_ACTIONERS_UPDATE = "org_fixed_actioners:update"
    ORG_FIXED_ACTIONERS_DELETE = "org_fixed_actioners:delete"

    # Change Request Permissions
    CHANGE_REQUEST_CREATE = "change_request:create"
    CHANGE_REQUEST_READ = "change_request:read"
    CHANGE_REQUEST_UPDATE = "change_request:update"
    CHANGE_REQUEST_DELETE = "change_request:delete"

    # Change Permissions
    CHANGE_CREATE = "change:create"
    CHANGE_READ = "change:read"
    CHANGE_UPDATE = "change:update"
    CHANGE_DELETE = "change:delete"
