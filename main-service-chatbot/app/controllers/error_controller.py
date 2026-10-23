"""Global FastAPI exception handlers, registered directly on the app in main.py.

Unlike the other modules in controllers/, this isn't business logic behind a
router - it's the app-wide error formatting layer, grouped here because it
plays the same "keep main.py thin" role the router/controller split does.
"""
import logging

from app.models.common import ErrorResponse
from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse

# Get a logger instance
logger = logging.getLogger(__name__)


async def http_exception_handler(request: Request, exc: HTTPException):
    """
    Handles FastAPI's built-in HTTPException.

    Uses the status_code, detail and headers from the HTTPException: a 429's Retry-After and a 401's
    WWW-Authenticate are part of the response, not decoration.
    Logs the error, typically as a warning for 4xx errors and error for 5xx.
    """
    log_level = logging.ERROR if exc.status_code >= 500 else logging.WARNING
    logger.log(
        log_level,
        f"HTTPException occurred: {exc.detail} "
        f"(Handler: http_exception_handler, Path: {request.url.path}, Method: {request.method})",
        exc_info=False
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(detail=exc.detail).model_dump(mode="json"),
        headers=getattr(exc, "headers", None),
    )


async def generic_exception_handler(request: Request, exc: Exception):
    """
    Handles any uncaught exception (catch-all).

    Logs the full exception traceback as an error.
    Returns a generic 500 Internal Server Error response to the client
    to avoid leaking sensitive internal details.
    """
    logger.exception(
        f"Unhandled exception occurred: {exc} "
        f"(Handler: generic_exception_handler, Path: {request.url.path}, Method: {request.method})",
    )

    # Return a generic error message to the client
    generic_detail = "An unexpected internal server error occurred."
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorResponse(detail=generic_detail).model_dump(mode="json"),
    )
