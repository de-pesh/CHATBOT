"""Auth models."""
from datetime import datetime
from typing import Literal

import email_validator
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.logic.user_context import preferences_of

# Fixed, two-valued - see dbo.users.role (scripts/mssql/05_users.sql).
# 'admin' can upload/delete files and run ingestion (app/routers/file.py); 'user' can only
# chat. No roles/permissions tables: with only two roles and three gated endpoints, a role
# column and one `require_admin` check say everything a join through two more tables would.
Role = Literal["admin", "user"]


class Token(BaseModel):
    access_token: str
    token_type: str
    role: Role

class LoginRequest(BaseModel):
    email: EmailStr = Field(..., description="User's email address")
    password: str = Field(..., description="User's password")

class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., description="Current password of the user")
    new_password: str = Field(..., min_length=8, description="New password for the user")
    confirm_new_password: str = Field(..., description="Confirmation of the new password")


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

# Base model for common attributes including is_active (for DB representation and responses)
class UserBase(UserCore):
    is_active: bool = Field(True, description="Indicates if the user account is active")

# Model for updating an existing user (input)
class UserUpdate(BaseModel):
    first_name: str | None = Field(None, min_length=1, max_length=255, description="First name of the user")
    last_name: str | None = Field(None, min_length=1, max_length=255, description="Last name of the user")
    email: EmailStr | None = Field(None, description="Email address of the user (must be unique)")
    is_active: bool | None = Field(None, description="Indicates if the user account is active")

# Input for POST /auth/users (admin-only). Separate from UserUpdate because password and role
# are required here and never present on a self-service update.
class UserCreate(UserCore):
    password: str = Field(..., min_length=8, description="Initial password for the new account")
    role: Role = Field("user", description="'admin' or 'user' - see the Role note above")
    is_active: bool = Field(True, description="Indicates if the user account is active")

# What POST /auth/users returns: everything about the new account except its password hash.
class UserPublic(UserBase):
    id: str | int = Field("33333333-3333-3333-3333-333333333333", description="Unique GUID identifier of the user")
    entity_id: str = Field("11111111-1111-1111-1111-111111111111", description="Entity GUID")
    tenant_id: str = Field("22222222-2222-2222-2222-222222222222", description="Tenant GUID")
    user_id: str = Field("33333333-3333-3333-3333-333333333333", description="User GUID")
    user_type: str = Field("demo_user", description="Type of user")
    is_admin: bool = Field(False, description="Is user admin")
    role: Role = Field("user", description="'admin' or 'user' - see the Role note above")
    created_on: datetime = Field(default_factory=datetime.utcnow, description="Timestamp when the user record was created")

# Model for user data as stored in the database (includes DB-generated fields)
class UserInDB(UserBase):
    id: str | int = Field("33333333-3333-3333-3333-333333333333", description="Unique GUID identifier of the user")
    entity_id: str = Field("11111111-1111-1111-1111-111111111111", description="Entity GUID")
    tenant_id: str = Field("22222222-2222-2222-2222-222222222222", description="Tenant GUID")
    user_id: str = Field("33333333-3333-3333-3333-333333333333", description="User GUID")
    user_type: str = Field("demo_user", description="Type of user")
    is_admin: bool = Field(False, description="Is user admin")
    hashed_password: str = Field("", description="Hashed password for the user")
    role: Role = Field("user", description="'admin' or 'user' - see the Role note above")
    display_name: str | None = Field(None, description="How the assistant addresses the user (PATCH /chat/me).")
    preferences: dict[str, str] = Field(
        default_factory=dict,
        description="Short facts the assistant may use to fit its answers to the user, e.g. "
                    "{'role': 'Compliance officer'} (PATCH /chat/me).",
    )
    created_on: datetime = Field(default_factory=datetime.utcnow, description="Timestamp when the user record was created")
    created_by: str = Field("system", description="User who created this user record")
    updated_on: datetime | None = Field(None, description="Timestamp when the user record was last updated")
    updated_by: str | None = Field(None, description="User who last updated this user record")

    # dbo.users.preferences comes back from the database as a JSON string (or None)
    @field_validator("preferences", mode="before")
    @classmethod
    def _parse_preferences(cls, value):
        return preferences_of(value)

class CurrentUser(UserInDB):
    @model_validator(mode="after")
    def _sync_is_admin(self):
        if self.role == "admin":
            self.is_admin = True
        return self
