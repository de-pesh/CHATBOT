import logging
import aiohttp
from fastapi import HTTPException, Request, status
from app.services.database_service import DatabaseService


logger = logging.getLogger(__name__)

def get_db_service(request: Request) -> DatabaseService:
    """Dependency that provides the database service instance from the application state."""
    return request.app.state.db_service


async def get_shared_http_client(request: Request) -> aiohttp.ClientSession:
    """ Dependency to get the shared aiohttp client session. """
    if not hasattr(request.app.state, "http_client") or request.app.state.http_client is None:
        logger.error("Shared aiohttp client session not found or not initialized!")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="HTTP client service is not available (not initialized)"
        )
    if request.app.state.http_client.closed:
        logger.error("Attempted to use a closed shared aiohttp client session!")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="HTTP client service is currently unavailable (session closed)"
        )
    return request.app.state.http_client