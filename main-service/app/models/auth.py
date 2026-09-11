"""Auth models.

Merged from: auth_models, user_models, role_models, permission_models, role_permission_models, user_role_assignment_permission_models.
"""
from datetime import datetime
from pydantic import BaseModel, Field
from pydantic import EmailStr
from typing import List, Optional
import email_validator


# --- from auth_models.py ---
class Token(BaseModel):
    access_token: str
    token_type: str
    permissions: List[str]

class LoginRequest(BaseModel):
    email: EmailStr = Field(..., description="User's email address")
    password: str = Field(..., description="User's password")

class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., description="Current password of the user")
    new_password: str = Field(..., min_length=8, description="New password for the user")
    confirm_new_password: str = Field(..., description="Confirmation of the new password")


# --- from user_models.py ---
# The local dev seed user is admin@ccpm.local. email-validator rejects `.local`
# as a special-use/reserved TLD, which makes every EmailStr parse of that row
# fail. Allow it so existing seed data validates.
email_validator.SPECIAL_USE_DOMAIN_NAMES = [
    d for d in email_validator.SPECIAL_USE_DOMAIN_NAMES if d != 'local'
]

# Core attributes without is_active for creation input
class UserCore(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=255, description="First name of the user")
    last_name: str = Field(..., min_length=1, max_length=255, description="Last name of the user")
    email: EmailStr = Field(..., description="Email address of the user (must be unique)")
    organization_id: Optional[int] = Field(..., description="ID of the organization the user belongs to")

# Model for creating a new user (input)
class UserCreate(UserCore):
    password: str = Field(..., min_length=8, description="Password for the user")

# Base model for common attributes including is_active (for DB representation and responses)
class UserBase(UserCore):
    is_active: bool = Field(True, description="Indicates if the user account is active")

# Model for updating an existing user (input)
class UserUpdate(BaseModel):
    first_name: Optional[str] = Field(None, min_length=1, max_length=255, description="First name of the user")
    last_name: Optional[str] = Field(None, min_length=1, max_length=255, description="Last name of the user")
    email: Optional[EmailStr] = Field(None, description="Email address of the user (must be unique)")
    organization_id: Optional[int] = Field(None, description="ID of the organization the user belongs to")
    is_active: Optional[bool] = Field(None, description="Indicates if the user account is active")

# Model for user data as stored in the database (includes DB-generated fields)
class UserInDB(UserBase):
    id: int = Field(..., description="Unique identifier of the user (UUID)")
    hashed_password: str = Field(..., description="Hashed password for the user")
    created_on: datetime = Field(..., description="Timestamp when the user record was created")
    created_by: str = Field(..., description="User who created this user record")
    updated_on: Optional[datetime] = Field(None, description="Timestamp when the user record was last updated")
    updated_by: Optional[str] = Field(None, description="User who last updated this user record")
    role_id: Optional[int] = Field(None, description = "Unique identifier of the role assigned to the user")
    role_name: Optional[str] = Field(None, description="Name of the role assigned to the user")

    class Config:
        from_attributes = True

# Model for API response (Excluding hashed_password for security)
# --- CHANGED: New Wrapper model for pagination ---
class CurrentUser(UserInDB):
    permissions: List[str] = Field(default_factory=list, description="List of permissions assigned to the user")


# --- from role_models.py ---
# Base model for role creation
# Base model for common attributes
# Model for creating a new role (input)
# Model for updating an existing role (input)
# Model for role data as stored in the database (includes DB-generated fields)
# --- from permission_models.py ---
# Base model for permission creation
# Base model for common attributes
# Model for creating a new permission (input)
# Model for updating an existing permission (input)
# Model for permission data as stored in the database (includes DB-generated fields)
# Model for API response (can be same as InDB or a subset)
# --- from role_permission_models.py ---
# Base model for common attributes
# Model for creating multiple role-permission assignments for a single role
# Model for synchronizing role-permission assignments for a role
# Model for updating an existing role-permission assignment (input)
# Model for role-permission assignment data as stored in the database (includes DB-generated fields)
# Model for API response (can be same as InDB or a subset)
# --- from user_role_assignment_permission_models.py ---
# Model for creating new user-role-permission assignments (input for the API)
# Base model representing a single user-role-permission assignment's core fields
# Model for updating an existing user-role-permission assignment (input)
# Model for a single user-role-permission assignment as stored in the database