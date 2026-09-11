from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from datetime import datetime, timezone
from fastapi import HTTPException, status

from app.config import Settings


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/authenticate")


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