"""Authentication and password hashing: who the caller is."""
from datetime import UTC, datetime, timedelta

import bcrypt
from fastapi import HTTPException, status
from jose import JWTError, jwt

from app.config import Settings, get_settings

settings: Settings = get_settings()


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


def create_access_token(data: dict, expires_delta: timedelta | None = None):
    """
    Creates a JWT access token.
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
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
        
        expiration_datetime = datetime.fromtimestamp(expiration_timestamp, tz=UTC)
        if expiration_datetime < datetime.now(UTC):
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
# Authorization is a plain role check now (see app.dependencies.require_admin) - with only
# two roles and three gated endpoints, a Permission vocabulary was a layer of indirection
# with nothing on the other end of it.
