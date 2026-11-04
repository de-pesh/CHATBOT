import logging
from datetime import datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from app.config import Settings, get_settings
from app.models.auth import CurrentUser
from app.security import decode_access_token
from app.services.db.database_service import DatabaseService
from app.services.queue.queue_service import MessageQueueService
from app.services.storage.storage_service import FileStorageService

# Header names for tenant context when DISABLE_AUTH=True (host-app / Module Federation mode).
_H_ENTITY_ID  = "x-entity-id"
_H_TENANT_ID  = "x-tenant-id"
_H_USER_ID    = "x-user-id"
_H_USER_TYPE  = "x-user-type"
_H_IS_ADMIN   = "x-is-admin"   # "true" → admin, anything else (including absent) → regular user

# Declares the login to OpenAPI so Swagger (/docs) shows an "Authorize" button that signs in through
# /auth/authenticate. It only documents: the token is still read by oauth2_scheme below, which stays lenient
# (a bare token without "Bearer" works) and never rejects a request itself.
_documented_login = OAuth2PasswordBearer(tokenUrl="/auth/authenticate", auto_error=False)


# Optional token extractor: returns the bearer token if present, otherwise None.
def oauth2_scheme(request: Request, _documented: str | None = Depends(_documented_login)) -> str | None:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth:
        return None
    auth = auth.strip()
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1]
    return auth


logger = logging.getLogger(__name__)


def get_db_service(request: Request) -> DatabaseService:
    """Dependency that provides the database service instance from the application state."""
    return request.app.state.db_service


def get_file_storage_service(request: Request) -> FileStorageService:
    """Dependency that provides the File Storage Service instance from the application state."""
    return request.app.state.file_storage_service


def get_kb_catalog(request: Request):
    """Dependency that provides the knowledge-base catalog, or None when there is no LLM to describe with."""
    return getattr(request.app.state, "kb_catalog", None)


def get_chatbot_queue(request: Request) -> MessageQueueService:
    """Dependency that provides the configured queue backend from the application state."""
    return request.app.state.redis_chatbot_queue


DEMO_ENTITY_ID = "11111111-1111-1111-1111-111111111111"
DEMO_TENANT_ID = "22222222-2222-2222-2222-222222222222"
DEMO_USER_ID = "33333333-3333-3333-3333-333333333333"


def _tenant_context_from_headers(request: Request, settings: Settings) -> dict:
    """Tenant / identity fields for the DISABLE_AUTH + TENANT_CONTEXT_ENABLED path.

    The host app (Module Federation / reverse proxy) is expected to inject these
    headers for every request.  Any missing header falls back to the demo value so
    a misconfigured host degrades gracefully rather than crashing.
    """
    if not getattr(settings, "TENANT_CONTEXT_ENABLED", False):
        return {}
    return {
        "entity_id": request.headers.get(_H_ENTITY_ID) or DEMO_ENTITY_ID,
        "tenant_id": request.headers.get(_H_TENANT_ID) or DEMO_TENANT_ID,
        "user_id":   request.headers.get(_H_USER_ID)   or DEMO_USER_ID,
        "user_type": request.headers.get(_H_USER_TYPE)  or "demo_user",
        "is_admin":  request.headers.get(_H_IS_ADMIN, "").lower() == "true",
    }


async def get_current_user(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    db_service: DatabaseService = Depends(get_db_service),
    settings: Settings = Depends(get_settings)
) -> CurrentUser:
    if getattr(settings, "DISABLE_AUTH", False):
        # Guard against header forgery when the API is reachable from the public ingress.
        # In platform-integration mode the proxy always injects X-Internal-Token; a browser
        # that reaches the API directly cannot know the secret and is rejected here.
        if getattr(settings, "TENANT_CONTEXT_ENABLED", False):
            secret = getattr(settings, "INTERNAL_API_SECRET", None)
            if secret is not None:
                provided = request.headers.get("x-internal-token", "")
                if provided != secret.get_secret_value():
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Missing or invalid internal token.",
                        headers={"WWW-Authenticate": "Bearer"},
                    )

        ctx = _tenant_context_from_headers(request, settings)
        # In platform-integration mode (TENANT_CONTEXT_ENABLED=True) the host app injects the
        # real user's GUID via X-User-Id. We use that GUID as the email/actor key so that session
        # and history ownership is isolated per real user — not shared across everyone under
        # one hardcoded "dev@local.dev" identity. In pure dev mode (TENANT_CONTEXT_ENABLED=False)
        # no headers are injected, so the single dev identity is intentional.
        actor = ctx.get("user_id") or DEMO_USER_ID
        is_admin = ctx.get("is_admin", True)  # dev mode: default admin; platform mode: from header
        # CurrentUser.email is an EmailStr — the actor (user_id) may be a plain
        # name or GUID without @. Append a synthetic domain so Pydantic accepts it
        # while still keeping per-user isolation (different actors → different emails).
        actor_email = actor if "@" in actor else f"{actor}@platform.local"
        return CurrentUser(
            id=actor,
            entity_id=ctx.get("entity_id", DEMO_ENTITY_ID),
            tenant_id=ctx.get("tenant_id", DEMO_TENANT_ID),
            user_id=actor,
            user_type=ctx.get("user_type", "demo_user"),
            is_admin=is_admin,
            first_name="Platform",
            last_name="User",
            email=actor_email,
            role="admin" if is_admin else "user",
            is_active=True,
            hashed_password="",
            created_on=datetime.utcnow(),
            created_by="system",
        )

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

    # When TENANT_CONTEXT_ENABLED, the login token carries entity/tenant context
    # (embedded by auth_controller.login_for_access_token). Fall back to demo values
    # if the claims are absent so tokens issued before the flag was turned on remain valid.
    tenant_enabled = getattr(settings, "TENANT_CONTEXT_ENABLED", False)
    jwt_entity_id = token_payload.get("entity_id", DEMO_ENTITY_ID) if tenant_enabled else DEMO_ENTITY_ID
    jwt_tenant_id = token_payload.get("tenant_id", DEMO_TENANT_ID) if tenant_enabled else DEMO_TENANT_ID
    jwt_user_type = token_payload.get("user_type", "demo_user")    if tenant_enabled else "demo_user"

    from app.services.db.stored_procedures import StoredProcedures

    try:
        user_results = await db_service.call_procedure(
            StoredProcedures.FILTER_USERS,
            [None, None, None, email, None, None, None, None]
        )
        if user_results and user_results[0] and user_results[0][0]:
            row = user_results[0][0]
            uid = row.get("id") if row.get("id") is not None else DEMO_USER_ID
            return CurrentUser(
                id=uid,
                entity_id=jwt_entity_id,
                tenant_id=jwt_tenant_id,
                user_id=str(uid),
                user_type=jwt_user_type,
                is_admin=(row.get("role") == "admin"),
                first_name=row.get("first_name", "Demo"),
                last_name=row.get("last_name", "User"),
                email=row.get("email", email),
                role=row.get("role", "user"),
                is_active=row.get("is_active", True),
                hashed_password=row.get("hashed_password", ""),
                display_name=row.get("display_name"),
                preferences=row.get("preferences") or {},
                created_on=row.get("created_on") or datetime.utcnow(),
                created_by=row.get("created_by", "system"),
            )
    except Exception:
        pass

    return CurrentUser(
        id=DEMO_USER_ID,
        entity_id=jwt_entity_id,
        tenant_id=jwt_tenant_id,
        user_id=DEMO_USER_ID,
        user_type=jwt_user_type,
        is_admin=True,
        first_name="Demo",
        last_name="User",
        email=email,
        role="admin",
        is_active=True,
        hashed_password="",
        created_on=datetime.utcnow(),
        created_by="system",
    )


def require_admin(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    settings: Settings = Depends(get_settings),
) -> bool:
    """Gates admin routes checking is_admin flag or role.

    Pure dev mode (DISABLE_AUTH=True, TENANT_CONTEXT_ENABLED=False): bypass, single dev identity.
    Platform-integration mode (DISABLE_AUTH=True, TENANT_CONTEXT_ENABLED=True): enforce is_admin —
    the host injects real user context and regular platform users must not reach admin routes.
    """
    disable_auth = getattr(settings, "DISABLE_AUTH", False)
    tenant_mode  = getattr(settings, "TENANT_CONTEXT_ENABLED", False)
    if disable_auth and not tenant_mode:
        return True  # pure local dev — single identity, no gate needed
    if not current_user.is_admin and current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only.")
    return True


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def require_real_accounts(settings: Settings = Depends(get_settings)) -> None:
    """Requirement for real accounts check."""
    if getattr(settings, "DISABLE_AUTH", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This view needs real accounts: it is unavailable while DISABLE_AUTH is on.",
        )


